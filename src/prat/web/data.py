"""Database reads that build the view models for pages.

Kept apart from the routes so pages, fragments and streams share one way of
loading a board or a card. Nothing here writes to the database.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prat import store
from prat.db import Card, Comment, Event, ImproveCycle, Project, Run, RunStep
from prat.domain import CardStatus, RunStatus, StepName, StepStatus
from prat.engine import Engine
from prat.git import DiffStats, Git, GitError, WorktreeManager
from prat.journal import read_journal
from prat.projects import ProjectSpec, project_file_at, show_project_file
from prat.web import views

LIVE_ENTRY_LIMIT = 300
MAX_DIFF_CHARS = 600_000


# Board ------------------------------------------------------------------------------


@dataclass
class Tile:
    id: int
    title: str
    status: str
    project_slug: str
    complexity: str
    attempts: int
    queued: bool
    step: str | None
    verdict: str | None
    updated_at: datetime | None


@dataclass
class Column:
    status: str
    tiles: list[Tile]

    @property
    def label(self) -> str:
        return views.status_label(self.status)

    @property
    def tone(self) -> str:
        return views.status_tone(self.status)


def latest_runs(session: Session, card_ids: Iterable[int]) -> dict[int, Run]:
    ids = list(card_ids)
    if not ids:
        return {}
    newest = (
        select(func.max(Run.id)).where(Run.card_id.in_(ids)).group_by(Run.card_id).scalar_subquery()
    )
    runs = session.scalars(select(Run).where(Run.id.in_(newest)))
    return {run.card_id: run for run in runs}


def run_counts(session: Session, card_ids: Iterable[int]) -> dict[int, int]:
    ids = list(card_ids)
    if not ids:
        return {}
    rows = session.execute(
        select(Run.card_id, func.count(Run.id)).where(Run.card_id.in_(ids)).group_by(Run.card_id)
    )
    return {card_id: count for card_id, count in rows}


def _verdict_of(run: Run | None) -> str | None:
    if run is None:
        return None
    for step in run.steps:
        if step.name == StepName.CRITIQUE and step.status == StepStatus.SUCCEEDED:
            verdict = (step.detail or {}).get("verdict")
            return str(verdict) if verdict else None
    return None


def _running_step(run: Run | None) -> str | None:
    if run is None or run.status != RunStatus.RUNNING:
        return None
    for step in run.steps:
        if step.status == StepStatus.RUNNING:
            return str(step.name)
    return None


def load_board(session: Session, engine: Engine, project: Project | None) -> list[Column]:
    cards = store.list_cards(session, project)
    runs = latest_runs(session, (c.id for c in cards))
    counts = run_counts(session, (c.id for c in cards))
    scheduled = engine.scheduled_cards()
    by_status: dict[str, list[Tile]] = {str(s): [] for s in views.BOARD_ORDER}
    for card in sorted(cards, key=lambda c: views.utc(c.updated_at) or c.created_at, reverse=True):
        run = runs.get(card.id)
        by_status[str(card.status)].append(
            Tile(
                id=card.id,
                title=card.title,
                status=str(card.status),
                project_slug=card.project.slug,
                complexity=str(card.complexity),
                attempts=counts.get(card.id, 0),
                queued=card.id in scheduled and card.status != CardStatus.RUNNING,
                step=_running_step(run),
                verdict=_verdict_of(run),
                updated_at=card.updated_at,
            )
        )
    return [Column(str(status), by_status[str(status)]) for status in views.BOARD_ORDER]


def board_signature(session: Session) -> tuple[Any, ...]:
    """Changes whenever anything a board shows changes."""
    last_event = session.scalar(select(func.max(Event.id))) or 0
    steps = session.execute(
        select(RunStep.run_id, RunStep.name, RunStep.status)
        .join(Run, Run.id == RunStep.run_id)
        .where(Run.status == RunStatus.RUNNING)
        .order_by(RunStep.id)
    ).all()
    return (last_event, tuple(tuple(map(str, row)) for row in steps))


# Card ----------------------------------------------------------------------------------


@dataclass
class RunView:
    id: int
    attempt: int
    status: str
    reason: str
    cost_usd: float
    budget_usd: float | None
    runtime: str
    started_at: datetime | None
    finished_at: datetime | None
    seconds: float | None
    feedback: str
    steps: list[views.StepView]
    journal_path: str | None


@dataclass
class VerdictView:
    run_id: int | None
    verdict: str
    summary: str
    confidence: float | None
    findings: list[dict[str, str]]
    fix_list: list[str]
    concerns: list[str]
    parsed: bool
    model: str
    cost_usd: float

    @property
    def label(self) -> str:
        return views.VERDICT_LABEL.get(self.verdict, self.verdict)

    @property
    def tone(self) -> str:
        return views.VERDICT_TONE.get(self.verdict, "neutral")


@dataclass
class GateView:
    name: str
    command: str
    passed: bool
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    seconds: float
    output: str
    log_path: str


@dataclass
class CommentView:
    id: int
    author: str
    kind: str
    body: str
    created_at: datetime | None
    run_id: int | None

    @property
    def author_label(self) -> str:
        return {"human": "You", "reviewer": "Reviewer", "system": "PRAT"}.get(
            self.author, self.author
        )


@dataclass
class HistoryItem:
    created_at: datetime | None
    text: str
    tone: str


@dataclass
class DiffView:
    files: list[views.FileDiff] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    commits: list[tuple[str, str]] = field(default_factory=list)
    base: str = ""
    head: str = ""
    error: str | None = None
    note: str | None = None

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)


@dataclass
class CardPage:
    card: Card
    project: Project
    criteria: list[str]
    runs: list[RunView]
    run: RunView | None
    verdict: VerdictView | None
    gates: list[GateView]
    findings: list[dict[str, Any]]
    comments: list[CommentView]
    history: list[HistoryItem]
    entries: list[views.EntryView]
    entries_hidden: int
    last_seq: int
    queued: bool
    live: bool
    actions: frozenset[str]
    diff: DiffView | None = None
    # Tables each project database changed on the card's branch.
    databases: list[dict[str, Any]] = field(default_factory=list)
    # What approve recorded, once the card has landed; and any undo since.
    landed: dict[str, Any] | None = None
    undone: dict[str, Any] | None = None
    # The newest lesson on the card (prat.lessons), already redacted when it was stored.
    lesson: dict[str, Any] | None = None

    @property
    def data_changed(self) -> bool:
        return any(d.get("tables") for d in self.databases)


def _step_views(run: Run) -> list[views.StepView]:
    items = []
    for step in run.steps:
        seconds = views.seconds_between(step.started_at, step.finished_at)
        items.append(
            views.StepView(
                name=str(step.name),
                status=str(step.status),
                attempts=step.attempts,
                started_at=step.started_at,
                finished_at=step.finished_at,
                seconds=seconds,
                error=views.redact(step.error or ""),
                facts=views.step_facts(str(step.name), step.detail or {}),
            )
        )
    longest = max((s.seconds or 0.0 for s in items), default=0.0)
    for item in items:
        item.percent = round(100 * (item.seconds or 0.0) / longest, 1) if longest else 0.0
    return items


def run_view(run: Run) -> RunView:
    return RunView(
        id=run.id,
        attempt=run.attempt,
        status=str(run.status),
        reason=views.redact(run.reason or ""),
        cost_usd=run.cost_usd,
        budget_usd=run.budget_usd,
        runtime=run.runtime,
        started_at=run.started_at,
        finished_at=run.finished_at,
        seconds=views.seconds_between(run.started_at, run.finished_at),
        feedback=run.feedback or "",
        steps=_step_views(run),
        journal_path=run.journal_path,
    )


def _verdict(comments: Sequence[Comment], run_id: int | None) -> VerdictView | None:
    candidates = [c for c in comments if c.author == "reviewer" and c.kind == "verdict"]
    chosen = next((c for c in reversed(candidates) if c.run_id == run_id), None)
    if chosen is None:
        return None
    data: Mapping[str, Any] = chosen.data or {}
    findings = []
    for item in data.get("findings") or []:
        if isinstance(item, Mapping):
            findings.append(
                {
                    "criterion": views.redact(str(item.get("criterion") or "")),
                    "status": str(item.get("status") or ""),
                    "evidence": views.redact(str(item.get("evidence") or "")),
                }
            )
    confidence = data.get("confidence")
    return VerdictView(
        run_id=chosen.run_id,
        verdict=str(data.get("verdict") or "partial"),
        summary=views.redact(str(data.get("summary") or chosen.body)),
        confidence=float(confidence) if isinstance(confidence, int | float) else None,
        findings=findings,
        fix_list=[views.redact(str(x)) for x in data.get("fix_list") or []],
        concerns=[views.redact(str(x)) for x in data.get("concerns") or []],
        parsed=bool(data.get("parsed", True)),
        model=str(data.get("model") or ""),
        cost_usd=float(data.get("cost_usd") or 0.0),
    )


def _lesson(comments: Sequence[Comment]) -> dict[str, Any] | None:
    lessons = [c for c in comments if c.kind == "lesson" and c.data]
    if not lessons:
        return None
    newest = lessons[-1]
    return {**dict(newest.data or {}), "run_id": newest.run_id, "older": len(lessons) - 1}


def _verify_detail(run: Run | None) -> tuple[list[GateView], list[dict[str, Any]]]:
    if run is None:
        return [], []
    for step in run.steps:
        if step.name != StepName.VERIFY:
            continue
        detail = step.detail or {}
        gates = [
            GateView(
                name=str(g.get("name") or ""),
                command=views.redact(str(g.get("command") or "")),
                passed=bool(g.get("passed")),
                exit_code=g.get("exit_code"),
                timed_out=bool(g.get("timed_out")),
                cancelled=bool(g.get("cancelled")),
                seconds=float(g.get("duration_seconds") or 0.0),
                output=views.redact(str(g.get("output_tail") or "")),
                log_path=str(g.get("log_path") or ""),
            )
            for g in detail.get("gates") or []
            if isinstance(g, Mapping)
        ]
        findings = [
            {k: views.redact(str(v)) if isinstance(v, str) else v for k, v in f.items()}
            for f in detail.get("findings") or []
            if isinstance(f, Mapping)
        ]
        return gates, findings
    return [], []


def _history(events: Sequence[Event]) -> list[HistoryItem]:
    items = []
    for event in events:
        data = event.data or {}
        reason = views.redact(str(data.get("reason") or ""))
        tone = "neutral"
        if event.kind == "card.added":
            text = f"Created as {views.status_label(data.get('status', 'draft')).lower()}"
        elif event.kind == "card.status":
            target = str(data.get("to"))
            tone = views.status_tone(target)
            text = f"{views.status_label(data.get('from'))} → {views.status_label(target)}"
            if reason:
                text += f": {reason}"
        elif event.kind == "run.status":
            target = str(data.get("to"))
            if target in ("running", "queued"):
                continue
            tone = views.status_tone(target)
            text = f"Run {event.run_id} {views.status_label(target).lower()}"
            if reason and target not in ("succeeded",):
                text += f": {reason}"
        elif event.kind == "card.merged":
            tone = "ok"
            text = f"Merged as {str(data.get('commit', ''))[:10]}"
            if data.get("databases"):
                text += " with its data"
        elif event.kind == "card.undone":
            tone = "warn"
            text = f"Undone: code reverted as {str(data.get('git_revert', ''))[:10]}"
            if data.get("databases"):
                text += ", data reverted"
        elif event.kind == "run.cancel_requested":
            tone = "warn"
            text = "Cancel requested"
        else:
            text = event.kind
        items.append(HistoryItem(event.created_at, text, tone))
    return items


def card_actions(card: Card, latest: Run | None, scheduled: bool) -> frozenset[str]:
    status = card.status
    actions: set[str] = set()
    if status == CardStatus.DRAFT:
        actions.add("ready")
    if not scheduled and status in (
        CardStatus.READY,
        CardStatus.REWORK,
        CardStatus.BLOCKED,
        CardStatus.FAILED,
    ):
        actions.add("run")
    if status in (CardStatus.REVIEW, CardStatus.BLOCKED, CardStatus.FAILED):
        actions.add("rework")
    if status == CardStatus.REVIEW:
        actions.add("approve")
    if status not in (CardStatus.DONE, CardStatus.CANCELLED):
        actions.add("cancel")
    if status == CardStatus.CANCELLED:
        actions.add("reopen")
    if (
        status == CardStatus.RUNNING
        and not scheduled
        and latest is not None
        and latest.status in (RunStatus.INTERRUPTED, RunStatus.RUNNING)
    ):
        actions.add("resume")
    return frozenset(actions)


def journal_entries(
    path: str | None, limit: int | None = None
) -> tuple[list[views.EntryView], int]:
    if not path:
        return [], 0
    entries = list(read_journal(Path(path)))
    presented = views.present_all(entries)
    if limit is not None and len(presented) > limit:
        return presented[-limit:], len(presented) - limit
    return presented, 0


def load_card(
    session: Session, engine: Engine, card_id: int, run_id: int | None = None
) -> CardPage:
    card = store.get_card(session, card_id)
    project = card.project
    runs = list(card.runs)
    latest = runs[-1] if runs else None
    selected = next((r for r in runs if r.id == run_id), None) if run_id else latest
    if run_id and selected is None:
        raise store.NotFound(f"card {card_id} has no run {run_id}")
    scheduled = engine.is_scheduled(card.id)
    comments = list(card.comments)
    gates, findings = _verify_detail(selected)
    entries, hidden = journal_entries(selected.journal_path if selected else None, LIVE_ENTRY_LIMIT)
    live = card.status == CardStatus.RUNNING or (
        scheduled and card.status in (CardStatus.READY, CardStatus.REWORK)
    )
    return CardPage(
        card=card,
        project=project,
        criteria=views.criteria_items(card.acceptance_criteria),
        runs=[run_view(r) for r in reversed(runs)],
        run=run_view(selected) if selected else None,
        verdict=_verdict(comments, selected.id if selected else None),
        gates=gates,
        findings=findings,
        comments=[
            CommentView(
                c.id,
                str(c.author),
                c.kind,
                c.body if c.author == "human" else views.redact(c.body),
                c.created_at,
                c.run_id,
            )
            for c in comments
        ],
        history=_history(store.events_for_card(session, card)),
        entries=entries,
        entries_hidden=hidden,
        last_seq=entries[-1].seq if entries else 0,
        queued=scheduled and card.status != CardStatus.RUNNING,
        live=live and (selected is None or selected is latest),
        actions=card_actions(card, latest, scheduled),
        databases=_databases_of(selected),
        landed=_last_event_data(session, card.id, "card.merged"),
        undone=_last_event_data(session, card.id, "card.undone"),
        lesson=_lesson(comments),
    )


def _databases_of(run: Run | None) -> list[dict[str, Any]]:
    if run is None:
        return []
    for step in run.steps:
        if step.name == StepName.VERIFY:
            found = (step.detail or {}).get("databases") or []
            return [d for d in found if isinstance(d, Mapping)]  # type: ignore[misc]
    return []


def _last_event_data(session: Session, card_id: int, kind: str) -> dict[str, Any] | None:
    event = session.scalars(
        select(Event)
        .where(Event.card_id == card_id, Event.kind == kind)
        .order_by(Event.id.desc())
        .limit(1)
    ).first()
    return dict(event.data or {}) if event is not None else None


@dataclass(frozen=True)
class DiffSource:
    agent_path: Path
    base: str
    head: str


def diff_source(card: Card, run: Run | None) -> DiffSource | None:
    """Where the card's changes can be read from: the run's base to the branch tip."""
    if run is None or not run.base_commit:
        return None
    head = run.head_commit if card.status == CardStatus.DONE and run.head_commit else card.branch
    head = head or run.head_commit
    if not head:
        return None
    return DiffSource(Path(card.project.agent_path), run.base_commit, head)


def load_diff(worktrees: WorktreeManager, source: DiffSource) -> DiffView:
    """Runs git; call it from a worker thread."""
    if not source.agent_path.exists():
        return DiffView(error="The agent clone does not exist yet.")
    try:
        stats: DiffStats = worktrees.diff_stats(source.agent_path, source.base, source.head)
        text = worktrees.diff_text(source.agent_path, source.base, stats.head)
    except GitError as exc:
        return DiffView(error=f"Could not read the diff: {views.redact(str(exc))}")
    note = None
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS]
        note = (
            f"The diff is larger than {views.fmt_bytes(MAX_DIFF_CHARS)}; later files are cut. "
            f"Run git diff {stats.base[:10]} {stats.head[:10]} in the agent clone for all of it."
        )
    return DiffView(
        files=views.build_diff(stats, text),
        insertions=stats.insertions,
        deletions=stats.deletions,
        commits=stats.commits,
        base=stats.base,
        head=stats.head,
        note=note,
    )


def card_signature(session: Session, engine: Engine, card_id: int) -> tuple[Any, ...]:
    """Changes whenever the card's panels would render differently (except the log)."""
    card = store.get_card(session, card_id)
    latest = store.latest_run(session, card)
    last_event = session.scalar(select(func.max(Event.id)).where(Event.card_id == card_id)) or 0
    comments = session.scalar(select(func.count(Comment.id)).where(Comment.card_id == card_id))
    steps: tuple[tuple[str, str], ...] = ()
    if latest is not None:
        steps = tuple((str(s.name), str(s.status)) for s in latest.steps)
    return (
        str(card.status),
        latest.id if latest else None,
        str(latest.status) if latest else None,
        steps,
        last_event,
        comments,
        engine.is_scheduled(card_id),
    )


# Projects ---------------------------------------------------------------------------


def registered_spec(project: Project) -> ProjectSpec:
    """The project file snapshot taken at registration."""
    values = dict(project.config or {})
    values.setdefault("name", project.name)
    values.setdefault("default_branch", project.default_branch)
    return ProjectSpec.model_validate(values)


def committed_project_file(agent_path: Path, branch: str) -> str | None:
    """The project file as committed on the default branch of the agent clone."""
    result = show_project_file(Git(agent_path), branch)
    return result.stdout if result.ok else None


@dataclass
class ProjectRow:
    project: Project
    counts: dict[str, int]
    total: int


def load_projects(session: Session) -> list[ProjectRow]:
    projects = store.list_projects(session)
    rows = session.execute(
        select(Card.project_id, Card.status, func.count(Card.id)).group_by(
            Card.project_id, Card.status
        )
    )
    counts: dict[int, Counter[str]] = {}
    for project_id, status, count in rows:
        counts.setdefault(project_id, Counter())[str(status)] = count
    result = []
    for project in projects:
        found = counts.get(project.id, Counter())
        ordered = {str(s): found[str(s)] for s in views.BOARD_ORDER if found[str(s)]}
        result.append(ProjectRow(project, ordered, sum(found.values())))
    return result


# Usage ------------------------------------------------------------------------------

USAGE_PERIODS = (7, 30, 90)
USAGE_ROLES = (
    ("planner", "Planning"),
    ("builder", "Building"),
    ("reviewer", "Review"),
    ("security", "Security review"),
    ("improve", "Self-improvement"),
    ("other", "Retries and other"),
)
USAGE_OUTCOMES = (
    ("landed", "Landed", "ok"),
    ("open", "In review or in progress", "accent"),
    ("unfinished", "Did not finish", "warn"),
)


@dataclass
class Share:
    key: str
    label: str
    usd: float = 0.0
    count: int = 0
    tone: str = "neutral"
    share: float = 0.0
    width: float = 0.0


@dataclass
class UsagePage:
    days: int
    total: float
    runs: int
    landed_cards: int
    per_landed: float | None
    roles: list[Share]
    outcomes: list[Share]
    projects: list[Share]
    daily: list[Share]
    cards: list[Share]


def _step_spend(run: Run) -> dict[str, float]:
    spend = dict.fromkeys(("planner", "builder", "reviewer", "security"), 0.0)
    for step in run.steps:
        detail = step.detail or {}
        amount = float(detail.get("cost_usd") or 0.0)
        if step.name == StepName.PLAN:
            spend["planner"] += amount
        elif step.name == StepName.EXECUTE:
            spend["builder"] += amount
        elif step.name == StepName.CRITIQUE:
            spend["reviewer"] += amount
            security = detail.get("security")
            if isinstance(security, Mapping):
                spend["security"] += float(security.get("cost_usd") or 0.0)
    return spend


def _outcome(run: Run) -> str:
    if run.status == RunStatus.SUCCEEDED and run.card.status == CardStatus.DONE:
        return "landed"
    if run.status in (RunStatus.SUCCEEDED, RunStatus.RUNNING, RunStatus.QUEUED):
        return "open"
    return "unfinished"


def _scaled(items: list[Share], total: float) -> list[Share]:
    largest = max((i.usd for i in items), default=0.0)
    for item in items:
        item.share = round(100 * item.usd / total, 1) if total else 0.0
        item.width = round(100 * item.usd / largest, 1) if largest else 0.0
    return items


def _by_usd(items: Iterable[Share]) -> list[Share]:
    return sorted(items, key=lambda s: s.usd, reverse=True)


def load_usage(session: Session, days: int, now: datetime) -> UsagePage:
    """Where model usage went over the last ``days`` days, valued at API prices."""
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    runs = list(session.scalars(select(Run).where(Run.created_at >= start).order_by(Run.id)))
    cycles = list(session.scalars(select(ImproveCycle).where(ImproveCycle.started_at >= start)))

    roles = {key: Share(key, label) for key, label in USAGE_ROLES}
    outcomes = {key: Share(key, label, tone=tone) for key, label, tone in USAGE_OUTCOMES}
    projects: dict[str, Share] = {}
    cards: dict[int, Share] = {}
    dates = [(start + timedelta(days=i)).date() for i in range(days)]
    daily = {day: Share(day.isoformat(), day.strftime("%b %d").replace(" 0", " ")) for day in dates}
    landed: set[int] = set()

    def add_day(when: datetime | None, usd: float) -> None:
        day = daily.get((views.utc(when) or now).date())
        if day is not None:
            day.usd += usd
            day.count += 1

    for run in runs:
        usd = run.cost_usd or 0.0
        spend = _step_spend(run)
        for key, amount in spend.items():
            roles[key].usd += amount
        roles["other"].usd += max(usd - sum(spend.values()), 0.0)
        outcome = outcomes[_outcome(run)]
        outcome.usd += usd
        outcome.count += 1
        card = run.card
        if outcome.key == "landed":
            landed.add(card.id)
        slug = card.project.slug
        projects.setdefault(slug, Share(slug, slug)).usd += usd
        projects[slug].count += 1
        entry = cards.setdefault(card.id, Share(str(card.id), f"#{card.id} {card.title}"))
        entry.usd += usd
        entry.count += 1
        add_day(run.created_at, usd)
    for cycle in cycles:
        roles["improve"].usd += cycle.cost_usd or 0.0
        roles["improve"].count += 1
        add_day(cycle.started_at, cycle.cost_usd or 0.0)

    total = sum(r.usd for r in roles.values())
    landed_spend = sum(cards[card_id].usd for card_id in landed)
    return UsagePage(
        days=days,
        total=total,
        runs=len(runs),
        landed_cards=len(landed),
        per_landed=landed_spend / len(landed) if landed else None,
        roles=_scaled([r for r in roles.values() if r.usd > 0], total),
        outcomes=_scaled(list(outcomes.values()), total),
        projects=_scaled(_by_usd(projects.values()), total),
        daily=_scaled(list(daily.values()), total),
        cards=_scaled(_by_usd(cards.values())[:10], total),
    )


# Getting started ----------------------------------------------------------------------


@dataclass
class SetupStep:
    key: str
    title: str
    detail: str
    done: bool
    href: str = ""
    action: str = ""


@dataclass
class SetupView:
    steps: list[SetupStep]

    @property
    def done_count(self) -> int:
        return sum(1 for s in self.steps if s.done)

    @property
    def complete(self) -> bool:
        return self.done_count == len(self.steps)

    @property
    def next_key(self) -> str | None:
        return next((s.key for s in self.steps if not s.done), None)


def _has_gates(project: Project) -> bool:
    """A project counts as set up once its checkout has a project file, or the
    registered snapshot already defines gates."""
    if project_file_at(Path(project.checkout_path)) is not None:
        return True
    return bool(registered_spec(project).verify.gates)


def load_setup(session: Session, *, signed_in: bool, sign_in_detail: str) -> SetupView:
    """The first-run checklist. Touches the file system; call it from a worker thread."""
    projects = store.list_projects(session)
    without_gates = next((p for p in projects if not _has_gates(p)), None)
    cards = session.scalar(select(func.count(Card.id))) or 0
    landed = session.scalar(select(func.count(Card.id)).where(Card.status == CardStatus.DONE)) or 0
    first = projects[0] if projects else None
    target = without_gates or first
    return SetupView(
        [
            SetupStep(
                "sign-in",
                "Agents can sign in",
                "Uses your Claude Code login or an API key."
                if signed_in
                else sign_in_detail or "Agents cannot sign in yet.",
                signed_in,
                "/settings",
                "Check sign-in",
            ),
            SetupStep(
                "project",
                "Add a project",
                "Point PRAT at a git checkout. It works in its own clone and never pushes.",
                bool(projects),
                "/projects/new",
                "Add a project",
            ),
            SetupStep(
                "gates",
                "Give it checks",
                "A committed .prat/project.toml with tests that must pass before review.",
                bool(projects) and without_gates is None,
                f"/projects/{target.slug}" if target else "",
                "Get a starter file",
            ),
            SetupStep(
                "card",
                "File your first card",
                "A goal and acceptance criteria. Mark it ready and an agent picks it up.",
                cards > 0,
                f"/cards/new?project={first.slug}" if first else "/cards/new",
                "New card",
            ),
            SetupStep(
                "approve",
                "Review and approve one",
                "Read the diff and the reviewer's verdict, then merge it into PRAT's clone.",
                landed > 0,
                "/board",
                "",
            ),
        ]
    )
