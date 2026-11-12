"""Dreams: what PRAT does in its person's quiet hours.

A dream runs in the dream window that :mod:`prat.rhythm` learned, at most once per window, and
always yields: before every phase (and between projects inside one) it checks that nobody has
started using PRAT and that no card a person filed is running or waiting. A dream that has to
stop is marked interrupted and the next window starts it again.

Phases, in order:

``tidy``      housekeeping that needs no model: card previews of finished cards are removed,
              agent clones are pruned of stale worktree records.
``remember``  project knowledge is brought up to date (:mod:`prat.knowledge.maintain`).
``reflect``   PRAT's own diagnostics: how its runs went, and its health.

Self-improvement keeps its own schedule (``prat improve``); the dream does not run it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from prat import rhythm
from prat.config import Settings
from prat.db import Card, Database, Project
from prat.db.models import Dream
from prat.domain import CardStatus
from prat.environments import preview_path, remove_preview
from prat.git import Git
from prat.knowledge.maintain import remember
from prat.model_registry import Role, resolve_model
from prat.runtime import AgentRuntime

log = logging.getLogger(__name__)

PHASES = ("tidy", "remember", "reflect")
TICK_SECONDS = 120.0


class Interrupted(Exception):
    pass


@dataclass
class DreamResult:
    dream_id: int
    status: str
    phases: dict[str, Any] = field(default_factory=dict)


def _person_cards_busy(db: Database) -> bool:
    with db.session() as session:
        busy = session.scalars(
            select(Card.id)
            .join(Project, Project.id == Card.project_id)
            .where(Project.is_self.is_(False), Card.status == CardStatus.RUNNING)
        ).first()
    return busy is not None


def should_yield(db: Database, *, forced: bool = False) -> bool:
    """Whether a dream must stop now: someone is using PRAT, or a person's card runs."""
    if _person_cards_busy(db):
        return True
    return not forced and rhythm.state(db).name != "dreaming"


def tidy(db: Database, settings: Settings) -> dict[str, Any]:
    removed, pruned = 0, 0
    with db.session() as session:
        projects = [(p.slug, Path(p.agent_path)) for p in session.scalars(select(Project))]
        finished = [
            (c.id, c.project.slug, Path(c.project.agent_path))
            for c in session.scalars(
                select(Card).where(Card.status.in_((CardStatus.DONE, CardStatus.CANCELLED)))
            )
        ]
    for card_id, slug, agent in finished:
        path = preview_path(settings.home, slug, card_id)
        if path.exists():
            with contextlib.suppress(Exception):
                remove_preview(agent, path)
                removed += 1
    for _slug, agent in projects:
        if agent.exists() and Git(agent).run("worktree", "prune", check=False).ok:
            pruned += 1
    return {"previews_removed": removed, "clones_pruned": pruned}


def reflect(db: Database, settings: Settings, runtime_name: str) -> dict[str, Any]:
    from prat.improve.diagnostics import diagnose

    diagnosis = diagnose(db, settings, window_days=7, runtime=runtime_name)
    return {
        "health": [check.to_dict() for check in diagnosis.health],
        "findings": [d.title for d in diagnosis.findings],
    }


def start_dream(db: Database, window: rhythm.Window | None, *, trigger: str) -> int:
    with db.session() as session:
        row = Dream(
            trigger=trigger,
            window_start=window.start.isoformat() if window else "",
            window_end=window.end.isoformat() if window else "",
            status="running",
            phase=PHASES[0],
            results={},
        )
        session.add(row)
        session.flush()
        return row.id


def _finish(db: Database, dream_id: int, **values: Any) -> None:
    with db.session() as session:
        row = session.get(Dream, dream_id)
        if row is not None:
            for key, value in values.items():
                setattr(row, key, value)


async def run_dream(
    db: Database,
    settings: Settings,
    runtime: AgentRuntime | None,
    *,
    trigger: str = "schedule",
    runtime_name: str = "claude",
    forced: bool = False,
    window: rhythm.Window | None = None,
) -> DreamResult:
    """One dream. ``forced`` (a person asked for it) ignores the window but still yields to a
    person's running card."""
    dream_id = start_dream(db, window, trigger=trigger)
    results: dict[str, Any] = {}
    cost = 0.0

    def stop() -> bool:
        return should_yield(db, forced=forced)

    try:
        for phase in PHASES:
            if stop():
                raise Interrupted(phase)
            _finish(db, dream_id, phase=phase)
            if phase == "tidy":
                results[phase] = await asyncio.to_thread(tidy, db, settings)
            elif phase == "remember":
                model = resolve_model(Role.LIBRARIAN, settings_models=settings.models)
                # Checking entries against the code is reading, so it gets the reader's model.
                checker = resolve_model(Role.READER, settings_models=settings.models)
                report = await remember(
                    db,
                    settings.home,
                    runtime,
                    model=model,
                    check_model=checker,
                    max_autonomy=settings.max_autonomy,
                    should_stop=stop,
                )
                cost += report.cost_usd
                results[phase] = report.to_dict()
            else:
                results[phase] = await asyncio.to_thread(reflect, db, settings, runtime_name)
            _finish(db, dream_id, results=dict(results), cost_usd=cost)
    except Interrupted as exc:
        _finish(
            db,
            dream_id,
            status="interrupted",
            results={**results, "interrupted_at": str(exc)},
            cost_usd=cost,
            finished_at=datetime.now(UTC).replace(tzinfo=None),
        )
        return DreamResult(dream_id, "interrupted", results)
    except Exception as exc:
        log.exception("dream %s failed", dream_id)
        _finish(
            db,
            dream_id,
            status="failed",
            results={**results, "error": str(exc)},
            cost_usd=cost,
            finished_at=datetime.now(UTC).replace(tzinfo=None),
        )
        return DreamResult(dream_id, "failed", results)
    _finish(
        db,
        dream_id,
        status="succeeded",
        results=results,
        cost_usd=cost,
        finished_at=datetime.now(UTC).replace(tzinfo=None),
    )
    return DreamResult(dream_id, "succeeded", results)


def dreamed_in(db: Database, window: rhythm.Window) -> bool:
    """Whether a scheduled dream already finished (or is running) in this window."""
    with db.session() as session:
        rows = session.scalars(
            select(Dream).where(
                Dream.window_start == window.start.isoformat(),
                Dream.status.in_(("running", "succeeded")),
            )
        ).first()
    return rows is not None


def recent_dreams(db: Database, limit: int = 10) -> list[Dream]:
    with db.session() as session:
        rows = list(session.scalars(select(Dream).order_by(Dream.id.desc()).limit(limit)))
        session.expunge_all()
    return rows


class DreamScheduler:
    """Starts a dream when the window opens; one at a time. Runs inside ``prat serve``."""

    def __init__(
        self,
        db: Database,
        settings: Settings,
        runtime: Callable[[], AgentRuntime | None],
        *,
        runtime_name: str = "claude",
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.runtime_name = runtime_name
        self.tick_seconds = tick_seconds
        self._loop: asyncio.Task[None] | None = None
        self._dream: asyncio.Task[DreamResult] | None = None

    @property
    def dreaming(self) -> bool:
        return self._dream is not None and not self._dream.done()

    def start_now(self) -> bool:
        """A dream now, at a person's request. False if one is already running."""
        if self.dreaming:
            return False
        self._dream = asyncio.create_task(
            run_dream(
                self.db,
                self.settings,
                self.runtime(),
                trigger="manual",
                runtime_name=self.runtime_name,
                forced=True,
            ),
            name="prat-dream-manual",
        )
        return True

    async def tick(self) -> None:
        try:
            if self.dreaming:
                return
            current = rhythm.state(self.db)
            window = current.window
            if current.name != "dreaming" or window is None or dreamed_in(self.db, window):
                return
            if _person_cards_busy(self.db):
                return
            self._dream = asyncio.create_task(
                run_dream(
                    self.db,
                    self.settings,
                    self.runtime(),
                    runtime_name=self.runtime_name,
                    window=window,
                ),
                name="prat-dream",
            )
        except Exception:
            log.exception("dream tick failed")

    def start(self) -> None:
        if self._loop is None:
            self._loop = asyncio.create_task(self._run_forever(), name="prat-dream-scheduler")

    async def _run_forever(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.tick_seconds)

    async def stop(self) -> None:
        for task in (self._loop, self._dream):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._loop = None
