"""One self-improvement cycle: sync, diagnose, research, select and file cards.

A cycle is recorded in ``improve_cycles`` with its stage as it goes, and every
decision is written to the activity feed. Only one cycle runs at a time
(checked in the database, so a CLI run and the server cannot overlap). The
cycle never lands anything itself: it files cards, and the cards run, are
proven and land through the engine like any other card.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prat import store
from prat.config import Settings
from prat.db import Card, Database, Project, Run
from prat.db.models import ImproveCycle, ImproveFinding
from prat.domain import (
    CardStatus,
    CommentAuthor,
    Complexity,
    CycleStage,
    CycleStatus,
    CycleTrigger,
    Effort,
    FindingSource,
    FindingStatus,
)
from prat.errors import record_error
from prat.improve.diagnostics import diagnose
from prat.improve.events import EventKind, record_event
from prat.improve.findings import (
    SCREENED_PREFIX,
    FindingDraft,
    mark_stale,
    record_findings,
    was_let_through,
)
from prat.improve.landing import check_outcomes, revive_failed_findings
from prat.improve.research import run_research
from prat.improve.roadmap import read_roadmap
from prat.improve.selfproject import (
    SelfProjectError,
    get_self_project,
    load_self_info,
    sync_self_project,
)
from prat.improve.settings import ImproveSettings, load_improve_settings
from prat.model_registry import Role, resolve_model
from prat.runtime import AgentRuntime

log = logging.getLogger(__name__)

STANDARD_CRITERIA = (
    "the full test suite, lint and type check pass",
    "new behaviour is covered by tests",
)
# Card statuses that count as finished for the open-card cap.
FINISHED = (CardStatus.DONE, CardStatus.CANCELLED)
URGENT_RULES = ("health", "error")
MAX_GOAL_CHARS = 6000

Submit = Callable[[int], object]


@dataclass
class CycleResult:
    cycle_id: int | None
    status: CycleStatus
    message: str
    cards: list[int] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# Budget and caps ----------------------------------------------------------------------


def day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def spent_today(session: Session, now: datetime | None = None) -> float:
    """Research cost plus the cost of runs on self cards, since midnight UTC."""
    since = day_start(now or datetime.now(UTC))
    research = session.scalar(
        select(func.coalesce(func.sum(ImproveCycle.cost_usd), 0.0)).where(
            ImproveCycle.started_at >= since
        )
    )
    runs = session.scalar(
        select(func.coalesce(func.sum(Run.cost_usd), 0.0))
        .join(Card, Run.card_id == Card.id)
        .join(Project, Card.project_id == Project.id)
        .where(Project.is_self.is_(True), Run.created_at >= since)
    )
    return float(research or 0.0) + float(runs or 0.0)


def _text(row: ImproveFinding) -> tuple[str, str, str, str]:
    return (row.title, row.problem, row.proposal, row.acceptance_criteria)


def _texts_by_fingerprint(
    session: Session, drafts: list[FindingDraft]
) -> dict[str, tuple[str, str, str, str]]:
    prints = [d.fingerprint for d in drafts]
    if not prints:
        return {}
    rows = session.scalars(select(ImproveFinding).where(ImproveFinding.fingerprint.in_(prints)))
    return {row.fingerprint: _text(row) for row in rows}


def open_self_cards(session: Session) -> list[Card]:
    return list(
        session.scalars(
            select(Card)
            .join(Project, Card.project_id == Project.id)
            .where(Project.is_self.is_(True), Card.status.not_in(FINISHED))
            .order_by(Card.id)
        )
    )


# Ranking ------------------------------------------------------------------------------


def rank_key(finding: ImproveFinding) -> tuple[int, int, int, int]:
    """Health and repeated errors first, then other diagnostics, the roadmap in
    file order, then research by severity and effort."""
    rule = str((finding.evidence or {}).get("rule", ""))
    if finding.source == FindingSource.DIAGNOSTIC:
        group = 0 if rule in URGENT_RULES else 1
        return (group, finding.severity.rank, -finding.seen_count, finding.id)
    if finding.source == FindingSource.ROADMAP:
        position = (finding.evidence or {}).get("roadmap_position", 10_000)
        return (2, int(position) if isinstance(position, int) else 10_000, 0, finding.id)
    return (3, finding.severity.rank, finding.effort.rank, finding.id)


def ranked_open_findings(session: Session) -> list[ImproveFinding]:
    rows = session.scalars(select(ImproveFinding).where(ImproveFinding.status == FindingStatus.NEW))
    return sorted(rows, key=rank_key)


# Filing cards ------------------------------------------------------------------------

_COMPLEXITY = {
    Effort.LOW: Complexity.LOW,
    Effort.MEDIUM: Complexity.MEDIUM,
    Effort.HIGH: Complexity.HIGH,
}


def card_criteria(finding: ImproveFinding) -> str:
    lines = [line for line in (finding.acceptance_criteria or "").splitlines() if line.strip()]
    lines += [f"- {text}" for text in STANDARD_CRITERIA]
    return "\n".join(lines)


def evidence_comment(finding: ImproveFinding) -> str:
    evidence = dict(finding.evidence or {})
    sources = [str(url) for url in evidence.pop("sources", []) or []]
    notes = [str(note) for note in evidence.pop("notes", []) or []]
    lines = [
        f"Filed by self-improvement from a {finding.source} finding (#{finding.id}), "
        f"severity {finding.severity}, effort {finding.effort}, seen {finding.seen_count} times."
    ]
    if evidence:
        lines += ["", "Evidence:"]
        lines += [f"- {key}: {value}" for key, value in sorted(evidence.items())]
    if sources:
        lines += ["", "Sources:", *(f"- {url}" for url in sources)]
    if notes:
        lines += ["", "Researcher notes:", *(f"- {note}" for note in notes)]
    return "\n".join(lines)


def file_card(
    session: Session,
    project: Project,
    finding: ImproveFinding,
    conf: ImproveSettings,
    cycle_id: int,
) -> Card:
    goal = f"{finding.problem}\n\n{finding.proposal}".strip()
    card = store.add_card(
        session,
        project,
        title=finding.title[:200],
        goal=goal[:MAX_GOAL_CHARS],
        acceptance_criteria=card_criteria(finding),
        complexity=_COMPLEXITY.get(finding.effort, Complexity.MEDIUM),
        ready=True,
        budget_usd=conf.card_budget_usd,
    )
    store.add_comment(
        session,
        card,
        evidence_comment(finding),
        author=CommentAuthor.SYSTEM,
        kind="evidence",
        data={"finding_id": finding.id, "source": str(finding.source)},
    )
    finding.status = FindingStatus.CARDED
    finding.card_id = card.id
    record_event(
        session,
        EventKind.CARD_FILED,
        f"card #{card.id} filed: {card.title}",
        cycle_id=cycle_id,
        card_id=card.id,
        data={"finding_id": finding.id, "source": str(finding.source)},
    )
    return card


# The cycle ----------------------------------------------------------------------------


def start_cycle(db: Database, trigger: CycleTrigger) -> tuple[int | None, str]:
    """Create the cycle row, or explain why a cycle cannot start."""
    with db.session() as session:
        conf = load_improve_settings(session)
        if not conf.enabled:
            return None, "self-improvement is off"
        if get_self_project(session) is None:
            return None, "self-improvement is not set up"
        running = session.scalars(
            select(ImproveCycle.id).where(ImproveCycle.status == CycleStatus.RUNNING)
        ).first()
        if running is not None:
            return None, f"cycle {running} is still running"
        cycle = ImproveCycle(trigger=trigger, status=CycleStatus.RUNNING, stage=CycleStage.DIAGNOSE)
        session.add(cycle)
        session.flush()
        record_event(
            session, EventKind.CYCLE_STARTED, f"{trigger} cycle started", cycle_id=cycle.id
        )
        return cycle.id, "started"


def _set(db: Database, cycle_id: int, **values: Any) -> None:
    with db.session() as session:
        cycle = session.get(ImproveCycle, cycle_id)
        if cycle is not None:
            for key, value in values.items():
                setattr(cycle, key, value)


def _enabled(db: Database) -> bool:
    with db.session() as session:
        return load_improve_settings(session).enabled


async def run_cycle(
    db: Database,
    settings: Settings,
    runtime: AgentRuntime,
    *,
    trigger: CycleTrigger = CycleTrigger.MANUAL,
    submit: Submit | None = None,
    now: Callable[[], datetime] | None = None,
) -> CycleResult:
    """Run one cycle to the end. Never raises: failures are recorded on the cycle."""
    clock = now or (lambda: datetime.now(UTC))
    cycle_id, reason = start_cycle(db, trigger)
    if cycle_id is None:
        return CycleResult(None, CycleStatus.SKIPPED, reason)
    summary: dict[str, Any] = {}
    try:
        result = await _run(db, settings, runtime, cycle_id, summary, submit, clock)
    except Exception as exc:
        log.exception("self-improvement cycle %s failed", cycle_id)
        record_error(
            db, "improve", type(exc).__name__, str(exc), exc=exc, detail={"cycle": cycle_id}
        )
        message = f"{type(exc).__name__}: {exc}"
        _set(
            db,
            cycle_id,
            status=CycleStatus.FAILED,
            error=message[:2000],
            finished_at=clock(),
            summary=summary,
        )
        with db.session() as session:
            record_event(session, EventKind.CYCLE_FAILED, message, cycle_id=cycle_id)
        return CycleResult(cycle_id, CycleStatus.FAILED, message, summary=summary)
    return result


async def _run(
    db: Database,
    settings: Settings,
    runtime: AgentRuntime,
    cycle_id: int,
    summary: dict[str, Any],
    submit: Submit | None,
    clock: Callable[[], datetime],
) -> CycleResult:
    with db.session() as session:
        conf = load_improve_settings(session)
        project = get_self_project(session)
        if project is None:
            raise SelfProjectError("self-improvement is not set up")
        clone, branch, project_id = Path(project.agent_path), project.default_branch, project.id
        project_models = dict((project.config or {}).get("models") or {})

    # Diagnose -----------------------------------------------------------------------
    sync = sync_self_project(db, settings, cycle_id=cycle_id)
    diagnosis = diagnose(
        db, settings, window_days=conf.window_days, runtime=runtime.name, sync=sync, now=clock()
    )
    # Landed fixes whose error came back are diagnostic findings too.
    diagnostic = [*diagnosis.findings, *check_outcomes(db, clock())]
    with db.session() as session:
        info = load_self_info(session)
    overlay_target = info is not None and info.is_overlay
    roadmap = read_roadmap(clone, branch)
    with db.session() as session:
        found = record_findings(session, diagnostic, cycle_id=cycle_id, now=clock())
        mark_stale(
            session,
            FindingSource.DIAGNOSTIC,
            [d.fingerprint for d in diagnostic],
            cycle_id=cycle_id,
        )
        planned = record_findings(
            session, [i.to_draft() for i in roadmap], cycle_id=cycle_id, now=clock()
        )
        mark_stale(
            session, FindingSource.ROADMAP, [i.fingerprint for i in roadmap], cycle_id=cycle_id
        )
    summary.update(
        {
            "sync": {"ok": sync.ok, "diverged": sync.diverged, "message": sync.message},
            "metrics": diagnosis.metrics,
            "health": [check.to_dict() for check in diagnosis.health],
            "diagnostic_findings": len(found),
            "new_diagnostic_findings": sum(1 for r in found if r.new),
            "roadmap_items": len(planned),
        }
    )
    _set(db, cycle_id, stage=CycleStage.RESEARCH, summary=summary)

    # Research -----------------------------------------------------------------------
    cost = 0.0
    with db.session() as session:
        remaining = conf.daily_budget_usd - spent_today(session, clock())
        known = [row.title for row in session.scalars(select(ImproveFinding))]
    if not conf.research_enabled:
        summary["research"] = "off"
    elif remaining <= 0:
        summary["research"] = "skipped: the daily budget is spent"
    else:
        research = await run_research(
            runtime,
            clone=clone,
            branch=branch,
            overlay=overlay_target,
            known_titles=known,
            model=resolve_model(
                Role.RESEARCHER, settings_models=settings.models, project_models=project_models
            ),
            budget_usd=min(conf.card_budget_usd, remaining),
            denied_roots=[settings.home / "worktrees", settings.journal_dir],
            stop=lambda: not _enabled(db),
        )
        cost = research.cost_usd
        if research.cancelled:
            _set(
                db,
                cycle_id,
                status=CycleStatus.CANCELLED,
                cost_usd=cost,
                finished_at=clock(),
                summary=summary,
            )
            with db.session() as session:
                record_event(
                    session,
                    EventKind.CYCLE_CANCELLED,
                    "self-improvement was turned off during research",
                    cycle_id=cycle_id,
                )
            return CycleResult(
                cycle_id, CycleStatus.CANCELLED, "turned off during research", summary=summary
            )
        with db.session() as session:
            if research.error:
                record_event(
                    session, EventKind.RESEARCH_REJECTED, research.error, cycle_id=cycle_id
                )
            added = record_findings(session, research.drafts, cycle_id=cycle_id, now=clock())
            # A person may have let a held finding through; that holds only for the
            # text they saw, so note it before research's new wording replaces it.
            seen_text = _texts_by_fingerprint(session, [d for d, _ in research.dismissed])
            screened = record_findings(
                session, [draft for draft, _ in research.dismissed], cycle_id=cycle_id, now=clock()
            )
            for recorded, (_, why) in zip(screened, research.dismissed, strict=True):
                row = session.get(ImproveFinding, recorded.finding_id)
                if row is None or row.status != FindingStatus.NEW:
                    continue
                if was_let_through(row) and seen_text.get(row.fingerprint) == _text(row):
                    continue
                row.status = FindingStatus.DISMISSED
                row.decision_note = f"{SCREENED_PREFIX}{why}"
                record_event(
                    session,
                    EventKind.FINDING_DISMISSED,
                    f"screened out ({why}): {row.title}",
                    cycle_id=cycle_id,
                    data={"finding_id": row.id, "reason": why},
                )
        if research.error:
            record_error(
                db, "improve", "research_rejected", research.error, detail={"cycle": cycle_id}
            )
        summary["research"] = {
            "proposals": len(added),
            "screened_out": len(screened),
            "notes": research.notes,
            "error": research.error,
            "cost_usd": round(cost, 6),
        }
    _set(db, cycle_id, stage=CycleStage.SELECT, cost_usd=cost, summary=summary)

    # Select and file ----------------------------------------------------------------
    filed: list[int] = []
    with db.session() as session:
        conf = load_improve_settings(session)
        revive_failed_findings(session, conf, clock())
        project = session.get(Project, project_id)
        open_cards = len(open_self_cards(session))
        spent = spent_today(session, clock())
        room = min(conf.max_new_cards_per_cycle, conf.max_open_cards - open_cards)
        if not conf.enabled:
            decision = "self-improvement was turned off"
        elif not sync.ok:
            decision = f"no cards filed: {sync.message}"
        elif spent >= conf.daily_budget_usd:
            decision = (
                f"no cards filed: ${spent:.2f} of the ${conf.daily_budget_usd:.2f} "
                "daily budget spent"
            )
        elif room <= 0:
            decision = (
                f"no cards filed: {open_cards} self cards are open (cap {conf.max_open_cards})"
            )
        elif project is None:
            decision = "no cards filed: the self project is gone"
        else:
            for finding in ranked_open_findings(session)[:room]:
                filed.append(file_card(session, project, finding, conf, cycle_id).id)
            decision = f"filed {len(filed)} card(s)"
    summary.update({"decision": decision, "cards": filed, "spent_today_usd": round(spent, 4)})
    if submit is not None:
        for card_id in filed:
            submit(card_id)

    _set(
        db,
        cycle_id,
        status=CycleStatus.SUCCEEDED,
        stage=CycleStage.DONE,
        finished_at=clock(),
        summary=summary,
    )
    with db.session() as session:
        record_event(
            session, EventKind.CYCLE_FINISHED, decision, cycle_id=cycle_id, data={"cards": filed}
        )
    return CycleResult(cycle_id, CycleStatus.SUCCEEDED, decision, filed, summary)


def fail_interrupted_cycles(db: Database) -> list[int]:
    """Cycles left ``running`` by a server that stopped are marked failed."""
    with db.session() as session:
        rows = list(
            session.scalars(select(ImproveCycle).where(ImproveCycle.status == CycleStatus.RUNNING))
        )
        for cycle in rows:
            cycle.status = CycleStatus.FAILED
            cycle.error = "server restarted"
            cycle.finished_at = datetime.now(UTC)
            record_event(session, EventKind.CYCLE_FAILED, "server restarted", cycle_id=cycle.id)
        return [cycle.id for cycle in rows]


def last_cycle_started(session: Session) -> datetime | None:
    started = session.scalar(
        select(func.max(ImproveCycle.started_at)).where(ImproveCycle.status != CycleStatus.SKIPPED)
    )
    return (
        started.replace(tzinfo=UTC) if started is not None and started.tzinfo is None else started
    )


def interval_elapsed(session: Session, conf: ImproveSettings, now: datetime) -> bool:
    last = last_cycle_started(session)
    return last is None or now - last >= timedelta(minutes=conf.interval_minutes)
