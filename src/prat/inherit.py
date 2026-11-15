"""Inherited memories: PRAT's record of work done before its agents kept memories of their own.

The crew got names and memories after PRAT had already worked many cards. That history is in
PRAT's database: every run, what the agent said when it finished, how many files it changed,
whether it landed, failed or was sent back, the person's feedback word for word, the reviewer's
verdict and the lesson written about it. ``inherit`` turns that record into episodes for the
agents who hold those jobs now, so their track records are true from the first day and they
learn from past rework instead of repeating it.

An inherited episode is marked as such (``Episode.inherited``). Its facts come only from the
record, and its feeling is only the one its outcome gives (prat.memory.OUTCOME_FEELINGS): no
thought or feeling is invented for an agent that did not live the work. The lead of each card is
the project's lead today (prat.crew.lead_for); the reviewer's verdicts go to the critic and
plans to the planner, as they would now. Running it again adds nothing already inherited, and
never touches a run the agent remembers for itself.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from prat import crew
from prat.db import Card, Database, Project, Run, as_utc
from prat.domain import CardKind, CardStatus, RunStatus, StepName
from prat.memory import (
    OUTCOME_FEELINGS,
    AgentMemory,
    Episode,
    Feeling,
    _safe,
    apply_outcome,
)

log = logging.getLogger(__name__)

WHAT_CHARS = 700


@dataclass
class Record:
    """What inherit found, per agent: episodes added and how they turned out."""

    added: dict[str, Counter[str]] = field(default_factory=dict)
    skipped: int = 0
    leads: dict[str, str] = field(default_factory=dict)

    def add(self, agent: str, outcome: str) -> None:
        self.added.setdefault(agent, Counter())[outcome] += 1

    @property
    def total(self) -> int:
        return sum(sum(c.values()) for c in self.added.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": {agent: dict(c) for agent, c in self.added.items()},
            "skipped": self.skipped,
            "leads": self.leads,
        }


def lead_key(db: Database, slug: str, checkout: Path) -> str:
    """The key of the project's lead today: its project file's ``[crew] lead``, else detected."""
    from prat.environments import committed_spec

    try:
        path, spec, _problem = committed_spec(db, slug)
        return crew.lead_for(spec.crew.model_dump(), path).key
    except Exception:
        log.debug("no committed project file for %s; detecting the stack", slug, exc_info=True)
        return crew.lead_for(None, checkout).key


def inherit(
    db: Database, home: Path, *, project: str | None = None, dry_run: bool = False
) -> Record:
    """Give each agent the episodes of past work it holds the job for now."""
    record = Record()
    with db.session() as session:
        query = select(Project).order_by(Project.id)
        if project is not None:
            query = query.where(Project.slug == project)
        projects = [(p.slug, Path(p.checkout_path)) for p in session.scalars(query)]
    if project is not None and not projects:
        raise LookupError(f"no project {project!r}")
    for slug, checkout in projects:
        lead = lead_key(db, slug, checkout)
        record.leads[slug] = lead
        with db.session() as session:
            cards = session.scalars(
                select(Card)
                .join(Project)
                .where(Project.slug == slug)
                .order_by(Card.id)
                .options(
                    selectinload(Card.runs).selectinload(Run.steps),
                    selectinload(Card.comments),
                )
            ).all()
            episodes = [e for card in cards for e in _card_episodes(slug, lead, card)]
        _save(home, episodes, record, dry_run=dry_run)
    return record


def describe(home: Path, record: Record) -> str:
    """One line per agent: who inherited what."""
    from prat import identity

    if not record.total:
        return f"Nothing new to inherit ({record.skipped} already remembered)."
    lines = []
    for agent, outcomes in sorted(record.added.items()):
        counts = ", ".join(f"{n} {outcome}" for outcome, n in outcomes.most_common())
        lines.append(f"{identity.of(home, agent).name} ({agent}): {counts}")
    lines.append(
        f"{record.total} episodes inherited"
        + (f", {record.skipped} already remembered." if record.skipped else ".")
    )
    return "\n".join(lines)


def _save(home: Path, episodes: list[Episode], record: Record, *, dry_run: bool) -> None:
    by_agent: dict[str, list[Episode]] = {}
    for episode in episodes:
        by_agent.setdefault(episode.agent, []).append(episode)
    for agent, mine in by_agent.items():
        memory = AgentMemory(home, agent)
        known = memory.episodes()
        ids = {e.id for e in known}
        lived = {(e.project, e.run_id) for e in known if not e.inherited and e.run_id}
        for episode in mine:
            if episode.id in ids or (episode.project, episode.run_id) in lived:
                record.skipped += 1
                continue
            if not dry_run:
                memory.save(episode)
            record.add(agent, episode.outcome)


def _card_episodes(slug: str, lead: str, card: Card) -> list[Episode]:
    runs = [r for r in card.runs if r.outcome is not None or r.finished_at is not None]
    if not runs:
        return []
    verdicts = {c.run_id: c for c in card.comments if c.kind == "verdict" and c.run_id}
    lessons = {c.run_id: c for c in card.comments if c.kind == "lesson" and c.run_id}
    role = "setup" if card.kind == CardKind.SETUP else "builder"
    found: list[Episode] = []
    for index, run in enumerate(runs):
        later = runs[index + 1] if index + 1 < len(runs) else None
        outcome, feedback, key = _outcome(card, run, later, first=index == 0)
        at = _moment(run)
        steps = {step.name: step for step in run.steps}
        execute = steps.get(StepName.EXECUTE)
        detail = dict(execute.detail or {}) if execute is not None else {}
        lesson, tags = _lesson(lessons.get(run.id))
        found.append(
            _episode(
                lead,
                slug,
                card,
                run,
                at,
                role=role,
                what=_what(detail, run),
                lesson=lesson,
                tags=tags,
                outcome=outcome,
                feedback=feedback,
                key=key,
            )
        )
        verdict = verdicts.get(run.id)
        if verdict is not None:
            data = verdict.data or {}
            word = str(data.get("verdict") or "given")
            fixes = [str(f) for f in data.get("fix_list") or []]
            summary = str(data.get("summary") or verdict.body)
            found.append(
                _episode(
                    "critic",
                    slug,
                    card,
                    run,
                    _moment(run, verdict.created_at),
                    role="critic",
                    what=f"Reviewed it and said {word}: {summary}"
                    + (f" Asked for: {'; '.join(fixes)}" if fixes else ""),
                    outcome=outcome,
                    feedback=feedback,
                    key=key,
                )
            )
        plan_step = steps.get(StepName.PLAN)
        plan = (plan_step.detail or {}).get("plan") if plan_step is not None else None
        if isinstance(plan, str) and plan.strip():
            found.append(
                _episode(
                    "planner",
                    slug,
                    card,
                    run,
                    at,
                    role="planner",
                    what=f"Planned it: {plan}",
                    outcome=outcome,
                    feedback=feedback,
                    key=key,
                )
            )
    return found


def _outcome(card: Card, run: Run, later: Run | None, *, first: bool) -> tuple[str, str, str]:
    """(outcome, feedback, feeling key) of one run, from the record."""
    reason = " ".join((run.reason or "").split())
    if later is not None:
        # Another run followed: this one was sent back (the person's words are on the next
        # run), or failed and was tried again.
        words = " ".join((later.feedback or "").split())
        if run.outcome == RunStatus.FAILED:
            return "failed", words or reason, "failed"
        if run.outcome == RunStatus.BLOCKED:
            return "blocked", words or reason, "blocked"
        if run.outcome == RunStatus.CANCELLED:
            return "cancelled", "", "cancelled"
        return "reworked", words, "reworked"
    status = CardStatus(card.status)
    if status == CardStatus.DONE:
        return "landed", "", "landed_first" if first else "landed"
    if status == CardStatus.CANCELLED or run.outcome == RunStatus.CANCELLED:
        return "cancelled", "", "cancelled"
    if status == CardStatus.FAILED or run.outcome == RunStatus.FAILED:
        return "failed", " ".join((card.status_reason or reason).split()), "failed"
    if status == CardStatus.BLOCKED or run.outcome == RunStatus.BLOCKED:
        return "blocked", reason, "blocked"
    return "pending", "", ""


def _episode(
    agent: str,
    slug: str,
    card: Card,
    run: Run,
    at: float,
    *,
    role: str,
    what: str,
    outcome: str,
    feedback: str,
    key: str,
    lesson: str = "",
    tags: list[str] | None = None,
) -> Episode:
    episode = Episode(
        id=f"rec-{run.id}-{_safe(agent)}",
        at=at,
        agent=_safe(agent),
        project=slug,
        card_id=card.id,
        run_id=run.id,
        role=role,
        title=card.title,
        what=" ".join(what.split())[:WHAT_CHARS],
        lesson=lesson,
        importance=0.5 if role != "critic" else 0.4,
        tags=tags or [],
        inherited=True,
    )
    if outcome != "pending":
        apply_outcome(episode, outcome, OUTCOME_FEELINGS.get(key, Feeling()), feedback)
    return episode


def _what(detail: dict[str, Any], run: Run) -> str:
    """What the agent did, in its own closing words from the run, and what changed."""
    parts = []
    files = detail.get("files_changed")
    if isinstance(files, int) and files:
        parts.append(f"Changed {files} files.")
    message = str(detail.get("message") or run.summary or "").strip()
    if message:
        parts.append(f"Said when it finished: {message}")
    elif run.reason:
        parts.append(f"The run ended: {run.reason}")
    return " ".join(parts) or "No record of what the run did."


def _lesson(comment: Any) -> tuple[str, list[str]]:
    """The lesson PRAT wrote about the run: its title and its trap."""
    if comment is None:
        return "", []
    data = comment.data or {}
    title = str(data.get("title") or "").strip()
    trap = " ".join(str(data.get("trap") or "").split())
    text = f"{title}. {trap}" if title and trap else title or trap
    return text[:500], [str(t) for t in data.get("tags") or []][:6]


def _moment(run: Run, stamp: datetime | None = None) -> float:
    for value in (stamp, run.finished_at, run.started_at, run.created_at):
        moment = as_utc(value)
        if moment is not None:
            return moment.timestamp()
    return 0.0
