"""Mission control: everything PRAT is doing, as one snapshot a terminal can draw.

``prat mission`` (see :mod:`prat.mission_app`) redraws it every couple of seconds. This module
only reads: the cards in flight with where each is on its route (prepare, plan, build, verify,
review, summary, finish) and what its agent last did; the cards waiting on the person (a
question, a review, a failure); every decision the policy refused; each project's environments
and databases; and the day's pulse (agent minutes by hour, cards landed, lessons written, the dream
window). Journals are read whole only when they change, and environments, which ask each
app's health URL, are refreshed less often than the rest.
"""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select

from prat import store
from prat.config import Settings
from prat.db import Card, Comment, Database, Event, Run
from prat.domain import STEP_ORDER, CardStatus, RunStatus, StepName, StepStatus
from prat.journal import read_first_and_last, read_journal
from prat.web import views

log = logging.getLogger(__name__)

# A card's route, in PRAT's own words (README: prepare, build, verify, review, ...).
STEP_LABELS: Mapping[StepName, str] = {
    StepName.PREPARE: "prepare",
    StepName.PLAN: "plan",
    StepName.EXECUTE: "build",
    StepName.VERIFY: "verify",
    StepName.CRITIQUE: "review",
    StepName.SUMMARIZE: "summary",
    StepName.FINALIZE: "finish",
}
# What an agent's recent activity shows: what it says and does, not bookkeeping.
ACTIVITY_CATEGORIES = frozenset({"text", "tool", "denied", "verify"})
ACTIVITY_LINES = 40
GUARD_RUNS = 20
GUARD_LIMIT = 40
ENVIRONMENT_SECONDS = 10.0
LANDED_LIMIT = 6
# The crew's records and the declared jobs change slowly; read them this often.
SLOW_SECONDS = 15.0
_ROLE_OF = {
    "critic": "reviewer",
    "planner": "planner",
    "security_reviewer": "security reviewer",
    "scout": "helper, scout",
    "tester": "helper, tester",
    "pair": "helper, pair",
    "data-analyst": "helper, data analyst",
    "web-checker": "helper, web checker",
    "reviewer": "helper, self-review",
}
# Waiting on the person, most urgent first: an agent's question, then work to approve.
WAITING_ORDER = (CardStatus.BLOCKED, CardStatus.REVIEW, CardStatus.FAILED)


@dataclass
class Leg:
    """One step of a card's route."""

    name: str
    state: str  # done, running, failed, skipped, pending
    seconds: float | None = None


@dataclass
class Activity:
    offset: float
    agent: str
    category: str
    tone: str
    title: str


@dataclass
class Flight:
    """A card an agent is working on, or one waiting for a slot."""

    card_id: int
    title: str
    project: str
    status: str
    queued: bool
    attempt: int
    route: list[Leg]
    since: datetime | None
    denials: int
    activity: list[Activity] = field(default_factory=list)
    # What the run's telemetry says the lead is doing (prat.engine.telemetry): its name, model,
    # task list, context fill, helpers, skills, last thought, reported progress.
    live: dict[str, Any] = field(default_factory=dict)
    # Why the governor holds it back, while it waits.
    hold: str | None = None
    parent_id: int | None = None

    @property
    def step(self) -> str | None:
        return next((leg.name for leg in self.route if leg.state == "running"), None)

    @property
    def lead(self) -> str:
        lead = self.live.get("lead")
        return str(lead.get("name")) if isinstance(lead, dict) else ""

    @property
    def lead_key(self) -> str:
        lead = self.live.get("lead")
        return str(lead.get("key") or "") if isinstance(lead, dict) else ""

    @property
    def tasks(self) -> list[dict[str, str]]:
        return [t for t in self.live.get("tasks") or [] if isinstance(t, dict)]

    @property
    def context_share(self) -> float | None:
        context = self.live.get("context")
        if not isinstance(context, dict) or not context.get("window"):
            return None
        return min(float(context.get("tokens") or 0) / float(context["window"]), 1.0)


@dataclass
class Waiting:
    """A card that needs the person: a question to answer, work to approve, a failure."""

    card_id: int
    title: str
    project: str
    status: str
    detail: str
    verdict: str | None = None
    gates_passed: int = 0
    gates_total: int = 0
    tables_changed: int = 0
    lesson: bool = False
    updated_at: datetime | None = None


@dataclass
class Refusal:
    """A decision the policy refused: the guardrail doing its job."""

    at: datetime
    card_id: int
    project: str
    agent: str
    title: str


@dataclass
class EnvironmentState:
    name: str
    state: str
    port: int | None


@dataclass
class ProjectLine:
    slug: str
    client: str | None
    branch: str
    databases: list[str]
    environments: list[EnvironmentState]
    open_cards: int
    problem: str | None = None


@dataclass
class Pulse:
    # Agent minutes in each of the last 24 hours, oldest first. Spend is not shown here: it has
    # one page of its own (web /usage), never a figure per card.
    busy_minutes: list[float]
    landed_today: int
    lessons_today: int
    runs_today: int
    dream: str | None


@dataclass
class Landing:
    """A card that landed: a checkpoint of its code and data."""

    at: datetime
    card_id: int
    title: str
    project: str
    commit: str
    databases: int


@dataclass
class CrewMember:
    """An agent with a memory: its record, its mood, what it is doing now."""

    agent: str
    who: str
    color: str
    episodes: int
    beliefs: int
    landed: int
    reworked: int
    mood: float
    working_on: list[int] = field(default_factory=list)
    last: str = ""
    # The name, bio and personality the agent chose for itself (prat.identity).
    name: str = ""
    bio: str = ""
    personality: str = ""


@dataclass
class UsageRow:
    model: str
    input: float
    output: float
    cache_read: float
    cache_write: float
    usd: float

    @property
    def cache_share(self) -> float | None:
        total = self.input + self.cache_read + self.cache_write
        return self.cache_read / total if total else None


@dataclass
class Snapshot:
    taken_at: datetime
    client: str | None
    clients: list[str]
    flights: list[Flight]
    waiting: list[Waiting]
    refusals: list[Refusal]
    projects: list[ProjectLine]
    pulse: Pulse
    landed: list[Landing] = field(default_factory=list)
    machine: str = ""
    crew: list[CrewMember] = field(default_factory=list)
    jobs: list[Any] = field(default_factory=list)  # prat.jobs.JobState
    declared_jobs: dict[str, list[str]] = field(default_factory=dict)
    epics: list[Any] = field(default_factory=list)  # prat.epics.EpicView
    usage: list[UsageRow] = field(default_factory=list)
    usage_by_role: dict[str, float] = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)
    review_waits: list[float] = field(default_factory=list)  # hours each review card has waited
    ideas_waiting: list[str] = field(default_factory=list)  # titles of the crew's open ideas

    @property
    def running(self) -> int:
        return sum(1 for f in self.flights if not f.queued)

    @property
    def queued(self) -> int:
        return sum(1 for f in self.flights if f.queued)

    def card(self, card_id: int) -> Flight | Waiting | None:
        items: list[Flight | Waiting] = [*self.flights, *self.waiting]
        return next((item for item in items if item.card_id == card_id), None)


def route_of(run: Run | None, now: datetime) -> list[Leg]:
    """Every step of PRAT's route with how far the run got along it."""
    steps = {step.name: step for step in run.steps} if run is not None else {}
    legs = []
    for name in STEP_ORDER:
        step = steps.get(name)
        state = "pending"
        seconds = None
        if step is not None:
            state = {
                StepStatus.SUCCEEDED: "done",
                StepStatus.RUNNING: "running",
                StepStatus.FAILED: "failed",
                StepStatus.SKIPPED: "skipped",
            }.get(step.status, "pending")
            began = views.utc(step.started_at)
            ended = views.utc(step.finished_at) or (now if state == "running" else None)
            if began is not None and ended is not None:
                seconds = max((ended - began).total_seconds(), 0.0)
        legs.append(Leg(STEP_LABELS[name], state, seconds))
    return legs


class MissionData:
    """Builds snapshots for one PRAT home, keeping what is costly to read between them."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        # Journal path -> (size, mtime, refusals in it).
        self._journals: dict[str, tuple[int, float, list[Refusal]]] = {}
        self._environments: dict[str, tuple[float, ProjectLine]] = {}
        self._crew_cache: tuple[float, list[CrewMember]] | None = None
        self._declared: tuple[float, dict[str, list[str]]] | None = None

    # Snapshot ---------------------------------------------------------------------------

    def snapshot(self, client: str | None = None, now: datetime | None = None) -> Snapshot:
        moment = now or datetime.now(UTC)
        with self.db.session() as session:
            clients = [c.slug for c in store.list_clients(session)]
            chosen = None
            if client is not None:
                try:
                    chosen = store.get_client(session, client)
                except store.NotFound:
                    client = None
            cards = store.list_cards(session, None, chosen)
            from prat.web.data import latest_runs

            runs = latest_runs(session, (c.id for c in cards))
            flights = [
                self._flight(card, runs.get(card.id), moment)
                for card in cards
                if card.status in (CardStatus.RUNNING, CardStatus.READY, CardStatus.REWORK)
            ]
            flights.sort(key=lambda f: (f.queued, f.card_id))
            waiting = [
                self._waiting(card, runs.get(card.id))
                for card in cards
                if card.status in WAITING_ORDER
            ]
            waiting.sort(
                key=lambda w: (
                    WAITING_ORDER.index(CardStatus(w.status)),
                    -(views.utc(w.updated_at) or moment).timestamp(),
                )
            )
            projects = store.list_projects(session, chosen)
            project_ids = {p.id for p in projects}
            refusals = self._refusals(session, project_ids if chosen else None, moment)
            landed = self._landed(session, project_ids if chosen else None)
            open_cards = {
                p.id: sum(
                    1
                    for c in cards
                    if c.project_id == p.id
                    and c.status not in (CardStatus.DONE, CardStatus.CANCELLED)
                )
                for p in projects
            }
            project_rows = [
                (p.slug, p.client.slug if p.client else None, p.default_branch, open_cards[p.id])
                for p in projects
            ]
            pulse = self._pulse(session, project_ids if chosen else None, moment)
            usage, by_role = self._usage(session, project_ids if chosen else None, moment)
            review_waits = [
                max((moment - (views.utc(c.updated_at) or moment)).total_seconds() / 3600, 0.0)
                for c in cards
                if c.status == CardStatus.REVIEW
            ]
            slugs = {p.slug for p in projects}
        lines = [self._project(*row) for row in project_rows]
        snap = Snapshot(moment, client, clients, flights, waiting, refusals, lines, pulse, landed)
        snap.machine = socket.gethostname()
        snap.usage, snap.usage_by_role, snap.review_waits = usage, by_role, review_waits
        snap.crew = self._crew(flights)
        snap.jobs, snap.declared_jobs = self._jobs(project_ids if chosen else None, slugs)
        from prat import epics

        snap.epics = epics.views(self.db, project_ids if chosen else None)
        from prat import alerts, ideas

        snap.ideas_waiting = [i.title for i in ideas.waiting(self.settings.home)]

        snap.alerts = alerts.from_snapshot(snap, self.settings)
        return snap

    # The crew, jobs, usage ------------------------------------------------------------------

    def _crew(self, flights: list[Flight]) -> list[CrewMember]:
        from prat import crew, identity, memory

        now = time.monotonic()
        if self._crew_cache is not None and now - self._crew_cache[0] < SLOW_SECONDS:
            members = self._crew_cache[1]
        else:
            members = []
            for agent in memory.all_agents(self.settings.home):
                store_ = memory.AgentMemory(self.settings.home, agent)
                stats = store_.stats()
                episodes = store_.episodes()
                lead = crew.MEMBERS.get(agent)
                who = identity.of(self.settings.home, agent)
                members.append(
                    CrewMember(
                        agent=agent,
                        who=f"lead, {lead.stack}" if lead else _ROLE_OF.get(agent, agent),
                        color=lead.color if lead else "",
                        episodes=stats["episodes"],
                        beliefs=stats["beliefs"],
                        landed=stats["outcomes"].get("landed", 0),
                        reworked=stats["outcomes"].get("reworked", 0),
                        mood=stats["mood"],
                        last=episodes[-1].title if episodes else "",
                        name=who.name,
                        bio=who.bio if who.chosen else "",
                        personality=who.personality if who.chosen else "",
                    )
                )
            self._crew_cache = (now, members)
        for member in members:
            member.working_on = [
                f.card_id
                for f in flights
                if not f.queued
                and (
                    f.lead_key == member.agent
                    or any(
                        h.get("name") == member.agent and h.get("state") == "running"
                        for h in f.live.get("helpers") or []
                    )
                )
            ]
        return members

    def _jobs(
        self, project_ids: set[int] | None, slugs: set[str]
    ) -> tuple[list[Any], dict[str, list[str]]]:
        from prat import jobs
        from prat.environments import committed_spec

        try:
            states = jobs.states(self.db, limit=20, project_ids=project_ids)
        except Exception:
            log.exception("could not read the jobs")
            states = []
        now = time.monotonic()
        if self._declared is None or now - self._declared[0] >= SLOW_SECONDS:
            declared: dict[str, list[str]] = {}
            for slug in sorted(slugs):
                try:
                    spec = committed_spec(self.db, slug)[1]
                except LookupError:
                    continue
                if spec.job:
                    declared[slug] = [j.name for j in spec.job]
            self._declared = (now, declared)
        return states, {k: v for k, v in self._declared[1].items() if k in slugs}

    def _usage(
        self, session: Any, project_ids: set[int] | None, now: datetime
    ) -> tuple[list[UsageRow], dict[str, float]]:
        """The last day's usage by model, and by kind of work (role)."""
        from prat.usage import merge

        since = (now - timedelta(hours=24)).replace(tzinfo=None)
        runs = list(
            session.scalars(select(Run).where(Run.created_at >= since, Run.usage.is_not(None)))
        )
        if project_ids is not None:
            runs = [r for r in runs if r.card.project_id in project_ids]
        roles: dict[str, float] = {}
        usages = []
        for run in runs:
            for role, data in ((run.usage or {}).get("roles") or {}).items():
                usages.append(data)
                spent = sum(float(v.get("usd") or 0) for v in (data.get("models") or {}).values())
                roles[str(role)] = roles.get(str(role), 0.0) + spent
        rows = [
            UsageRow(
                model,
                row["input"],
                row["output"],
                row["cache_read"],
                row["cache_write"],
                row["usd"],
            )
            for model, row in merge(usages).items()
        ]
        rows.sort(key=lambda r: r.usd, reverse=True)
        return rows, roles

    def _landed(self, session: Any, project_ids: set[int] | None) -> list[Landing]:
        query = select(Event).where(Event.kind == "card.merged").order_by(Event.id.desc())
        if project_ids is not None:
            query = query.where(Event.project_id.in_(project_ids))
        found = []
        for event in session.scalars(query.limit(LANDED_LIMIT)):
            card = session.get(Card, event.card_id) if event.card_id is not None else None
            if card is None:
                continue
            data = event.data or {}
            found.append(
                Landing(
                    at=views.utc(event.created_at) or event.created_at,
                    card_id=card.id,
                    title=card.title,
                    project=card.project.slug,
                    commit=str(data.get("commit") or "")[:10],
                    databases=len(data.get("databases") or []),
                )
            )
        return found

    # Cards ------------------------------------------------------------------------------

    def _flight(self, card: Card, run: Run | None, now: datetime) -> Flight:
        live = run is not None and run.status == RunStatus.RUNNING
        activity: list[Activity] = []
        denials = 0
        if live and run is not None and run.journal_path:
            first, last = read_first_and_last(Path(run.journal_path), 80)
            start = first.ts if first is not None else None
            shown = [views.present(entry, start) for entry in last]
            denials = sum(1 for e in shown if e.category == "denied")
            activity = [
                Activity(e.offset, e.agent, e.category, e.tone, describe(e))
                for e in shown
                if e.category in ACTIVITY_CATEGORIES
            ][-ACTIVITY_LINES:]
        return Flight(
            card_id=card.id,
            title=card.title,
            project=card.project.slug,
            status=str(card.status),
            queued=not live,
            attempt=run.attempt if run is not None else 0,
            route=route_of(run if live else None, now),
            since=views.utc(run.started_at) if live and run is not None else None,
            denials=denials,
            activity=activity,
            live=dict(run.live or {}) if live and run is not None else {},
            hold=card.hold_reason,
            parent_id=card.parent_id,
        )

    def _waiting(self, card: Card, run: Run | None) -> Waiting:
        from prat.web.data import _databases_of, _verdict_of, _verify_detail

        gates, _findings = _verify_detail(run)
        tables = sum(len(d.get("tables") or []) for d in _databases_of(run))
        if card.status == CardStatus.BLOCKED:
            detail = card.status_reason or "the agent asked a question"
        elif card.status == CardStatus.REVIEW:
            detail = card.summary or card.status_reason or "ready for your review"
        else:
            detail = card.status_reason or (run.reason if run is not None else None) or "failed"
        return Waiting(
            card_id=card.id,
            title=card.title,
            project=card.project.slug,
            status=str(card.status),
            detail=_one_line(detail),
            verdict=_verdict_of(run),
            gates_passed=sum(1 for g in gates if g.passed),
            gates_total=len(gates),
            tables_changed=tables,
            lesson=any(c.kind == "lesson" for c in card.comments),
            updated_at=card.updated_at,
        )

    # The guardrail feed -----------------------------------------------------------------

    def _refusals(self, session: Any, project_ids: set[int] | None, now: datetime) -> list[Refusal]:
        since = (now - timedelta(hours=24)).replace(tzinfo=None)
        query = (
            select(Run)
            .where(Run.created_at >= since, Run.journal_path.is_not(None))
            .order_by(Run.id.desc())
            .limit(GUARD_RUNS)
        )
        found: list[Refusal] = []
        for run in session.scalars(query):
            if project_ids is not None and run.card.project_id not in project_ids:
                continue
            found.extend(self._journal_refusals(run))
        found.sort(key=lambda r: r.at, reverse=True)
        return found[:GUARD_LIMIT]

    def _journal_refusals(self, run: Run) -> list[Refusal]:
        path = Path(str(run.journal_path))
        try:
            info = path.stat()
        except OSError:
            return []
        key = str(path)
        cached = self._journals.get(key)
        if cached is not None and cached[0] == info.st_size and cached[1] == info.st_mtime:
            return cached[2]
        refusals = []
        for entry in read_journal(path):
            shown = views.present(entry)
            if shown.category != "denied":
                continue
            refusals.append(
                Refusal(
                    at=views.utc(entry.ts) or entry.ts,
                    card_id=run.card_id,
                    project=run.card.project.slug,
                    agent=shown.agent,
                    title=describe(shown),
                )
            )
        self._journals[key] = (info.st_size, info.st_mtime, refusals)
        return refusals

    # Projects ---------------------------------------------------------------------------

    def _project(self, slug: str, client: str | None, branch: str, open_cards: int) -> ProjectLine:
        cached = self._environments.get(slug)
        if cached is not None and time.monotonic() - cached[0] < ENVIRONMENT_SECONDS:
            line = cached[1]
            line.open_cards = open_cards
            return line
        line = self._load_project(slug, client, branch, open_cards)
        self._environments[slug] = (time.monotonic(), line)
        return line

    def _load_project(
        self, slug: str, client: str | None, branch: str, open_cards: int
    ) -> ProjectLine:
        from prat.environments import (
            EnvironmentManager,
            committed_spec,
            load_local,
            merged,
        )

        try:
            _checkout, spec, problem = committed_spec(self.db, slug)
        except LookupError as exc:
            return ProjectLine(slug, client, branch, [], [], open_cards, str(exc))
        manager = EnvironmentManager(self.settings.home)
        envs = merged(list(spec.environment), load_local(self.settings.home, slug))
        states = []
        for env in envs:
            try:
                view = manager.view(slug, env)
            except OSError:
                continue
            states.append(EnvironmentState(env.name, view.state, view.port))
        databases = [f"{db.kind}" for db in spec.workspace.database]
        return ProjectLine(slug, client, branch, databases, states, open_cards, problem)

    # Pulse ------------------------------------------------------------------------------

    def _pulse(self, session: Any, project_ids: set[int] | None, now: datetime) -> Pulse:
        day_start = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        since = (now - timedelta(hours=24)).replace(tzinfo=None)
        query = select(Run).where(
            Run.started_at.is_not(None),
            or_(Run.finished_at.is_(None), Run.finished_at >= since),
        )
        runs = list(session.scalars(query))
        if project_ids is not None:
            runs = [r for r in runs if r.card.project_id in project_ids]
        # Agent minutes in each of the last 24 hours: how busy the slots were.
        hours = [0.0] * 24
        window_start = now - timedelta(hours=24)
        runs_today = 0
        for run in runs:
            began = views.utc(run.started_at) or now
            ended = views.utc(run.finished_at) or now
            if run.status != RunStatus.RUNNING and run.finished_at is None:
                ended = began
            if began >= day_start:
                runs_today += 1
            cursor = max(began, window_start)
            while cursor < ended:
                index = min(int((cursor - window_start).total_seconds() // 3600), 23)
                bucket_end = window_start + timedelta(hours=index + 1)
                step = min(ended, bucket_end)
                hours[index] += (step - cursor).total_seconds() / 60
                cursor = step if step > cursor else ended
        start = day_start.astimezone(UTC).replace(tzinfo=None)
        landed_query = select(func.count(Event.id)).where(
            Event.kind == "card.merged", Event.created_at >= start
        )
        lessons_query = select(func.count(Comment.id)).where(
            Comment.kind == "lesson", Comment.created_at >= start
        )
        if project_ids is not None:
            landed_query = landed_query.where(Event.project_id.in_(project_ids))
            lessons_query = lessons_query.join(Card).where(Card.project_id.in_(project_ids))
        return Pulse(
            busy_minutes=hours,
            landed_today=int(session.scalar(landed_query) or 0),
            lessons_today=int(session.scalar(lessons_query) or 0),
            runs_today=runs_today,
            dream=self._dream(now),
        )

    def _dream(self, now: datetime) -> str | None:
        from prat.rhythm import dream_window

        try:
            window = dream_window(self.db, now)
        except Exception:  # the pulse never fails over the dream window
            return None
        if window is None:
            return None
        start, end = window.start.strftime("%H:%M"), window.end.strftime("%H:%M")
        if window.contains(now.astimezone(window.start.tzinfo)):
            return f"dreaming until {end}"
        return f"dreams {start}-{end}" + (" (learned)" if window.learned else "")


def describe(entry: views.EntryView) -> str:
    """One line for a journal entry: the tool and its target, what the agent said, or what
    the policy refused and why."""
    if entry.category == "text":
        return _one_line(entry.detail)
    if entry.category == "denied":
        reason = f"({entry.detail})" if entry.detail else ""
        return _one_line(entry.code or entry.title, reason)
    return _one_line(entry.title, entry.code or entry.detail)


def _one_line(*parts: str | None) -> str:
    text = " ".join(p for p in parts if p)
    return " ".join(text.split())


def sparkline(values: Sequence[float]) -> str:
    """Eight-level bars for a short series; an empty series is blank."""
    bars = " ▁▂▃▄▅▆▇█"
    top = max(values, default=0.0)
    if top <= 0:
        return "▁" * len(values)
    return "".join(bars[max(1, round(8 * v / top))] if v > 0 else "▁" for v in values)


# Snapshots between machines ----------------------------------------------------------------


def to_json(snap: Snapshot) -> dict[str, Any]:
    """A snapshot as JSON-ready data, for another PRAT's mission control (/api/mission)."""
    from dataclasses import asdict

    def clean(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(k): clean(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [clean(v) for v in value]
        return value

    data = clean(asdict(snap))
    data["jobs"] = [
        {
            "run_id": j.run_id,
            "slug": j.slug,
            "name": j.name,
            "status": j.status,
            "stalled": j.stalled,
        }
        for j in snap.jobs
    ]
    data["epics"] = []
    return dict(data)


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


@dataclass
class RemoteDeck:
    """Another machine's PRAT, as its mission control reported it."""

    name: str
    flights: list[Flight] = field(default_factory=list)
    waiting: list[Waiting] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    error: str = ""


def remote_deck(name: str, data: dict[str, Any]) -> RemoteDeck:
    deck = RemoteDeck(name, alerts=[str(a) for a in data.get("alerts") or []])
    for item in data.get("flights") or []:
        deck.flights.append(
            Flight(
                card_id=int(item["card_id"]),
                title=str(item["title"]),
                project=str(item["project"]),
                status=str(item["status"]),
                queued=bool(item["queued"]),
                attempt=int(item.get("attempt") or 0),
                route=[
                    Leg(str(r["name"]), str(r["state"]), r.get("seconds"))
                    for r in item.get("route") or []
                ],
                since=_when(item.get("since")),
                denials=int(item.get("denials") or 0),
                live=dict(item.get("live") or {}),
                hold=item.get("hold"),
            )
        )
    for item in data.get("waiting") or []:
        deck.waiting.append(
            Waiting(
                card_id=int(item["card_id"]),
                title=str(item["title"]),
                project=str(item["project"]),
                status=str(item["status"]),
                detail=str(item.get("detail") or ""),
                verdict=item.get("verdict"),
                gates_passed=int(item.get("gates_passed") or 0),
                gates_total=int(item.get("gates_total") or 0),
                tables_changed=int(item.get("tables_changed") or 0),
                lesson=bool(item.get("lesson")),
            )
        )
    return deck


def fetch_remote(remote: dict[str, str], timeout: float = 5.0) -> RemoteDeck:
    import httpx

    name = remote.get("name") or remote.get("url") or "remote"
    url = (remote.get("url") or "").rstrip("/") + "/api/mission"
    try:
        response = httpx.get(
            url, headers={"Authorization": f"Bearer {remote.get('token', '')}"}, timeout=timeout
        )
        response.raise_for_status()
        return remote_deck(name, response.json())
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        return RemoteDeck(name, error=str(exc)[:200])
