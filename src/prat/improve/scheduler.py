"""The self-improvement scheduler that runs inside ``prat serve``.

Once a minute it decides whether a cycle may start (:func:`may_start`, the one
place that decision is made, so the rhythm module can replace it later), and
hands ready self cards to the engine. Nothing happens while the toggle is
off. Only asyncio is used: no cron, systemd, launchd or Task Scheduler.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from prat.config import Settings
from prat.db import Card, Database, Project, Run
from prat.db.models import ImproveCycle
from prat.domain import CardStatus, CycleStatus, CycleTrigger
from prat.engine import Engine
from prat.errors import record_error
from prat.improve.cycle import (
    CycleResult,
    fail_interrupted_cycles,
    interval_elapsed,
    run_cycle,
    spent_today,
)
from prat.improve.landing import LandingResult, Prover, tend_self_cards
from prat.improve.selfproject import get_self_project
from prat.improve.settings import load_improve_settings


def load_enabled(db: Database) -> bool:
    with db.session() as session:
        return load_improve_settings(session).enabled


log = logging.getLogger(__name__)

TICK_SECONDS = 60.0
RUNNABLE = (CardStatus.READY, CardStatus.REWORK)


@dataclass(frozen=True)
class Decision:
    start: bool
    reason: str


def may_start(db: Database, now: datetime | None = None) -> Decision:
    """Whether a scheduled cycle may start now, and why not when it may not."""
    when = now or datetime.now(UTC)
    with db.session() as session:
        conf = load_improve_settings(session)
        if not conf.enabled:
            return Decision(False, "self-improvement is off")
        if get_self_project(session) is None:
            return Decision(False, "self-improvement is not set up")
        running = session.scalar(
            select(func.count())
            .select_from(ImproveCycle)
            .where(ImproveCycle.status == CycleStatus.RUNNING)
        )
        if running:
            return Decision(False, "a cycle is running")
        if not interval_elapsed(session, conf, when):
            return Decision(False, "the interval since the last cycle has not passed")
        spent = spent_today(session, when)
        if spent >= conf.daily_budget_usd:
            return Decision(False, f"the daily budget is spent (${spent:.2f})")
    return Decision(True, "due")


def runnable_self_cards(db: Database) -> list[int]:
    """Ready or rework self cards that have not used up their attempts."""
    with db.session() as session:
        conf = load_improve_settings(session)
        if not conf.enabled:
            return []
        runs = (
            select(Run.card_id, func.count(Run.id).label("runs")).group_by(Run.card_id).subquery()
        )
        rows = session.execute(
            select(Card.id, func.coalesce(runs.c.runs, 0))
            .join(Project, Card.project_id == Project.id)
            .outerjoin(runs, runs.c.card_id == Card.id)
            .where(Project.is_self.is_(True), Card.status.in_(RUNNABLE))
            .order_by(Card.id)
        ).all()
    return [card_id for card_id, count in rows if count <= conf.max_reworks]


class ImproveScheduler:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        engine: Engine,
        *,
        tick_seconds: float = TICK_SECONDS,
        clock: Callable[[], datetime] | None = None,
        prover: Prover | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.engine = engine
        self.tick_seconds = tick_seconds
        self.clock = clock or (lambda: datetime.now(UTC))
        # None means the staged trial; tests pass a scripted prover.
        self.prover = prover
        self._loop: asyncio.Task[None] | None = None
        self._cycle: asyncio.Task[CycleResult] | None = None
        self._landing: asyncio.Task[list[LandingResult]] | None = None

    @property
    def cycle_running(self) -> bool:
        return self._cycle is not None and not self._cycle.done()

    @property
    def landing_running(self) -> bool:
        return self._landing is not None and not self._landing.done()

    def _start_landing(self) -> asyncio.Task[list[LandingResult]]:
        self._landing = asyncio.create_task(
            tend_self_cards(self.db, self.settings, self.engine, prover=self.prover),
            name="prat-improve-landing",
        )
        return self._landing

    def _submit(self, card_id: int) -> None:
        if self.engine.is_scheduled(card_id):
            return
        try:
            self.engine.submit(card_id)
        except Exception as exc:
            log.warning("could not submit self card %s: %s", card_id, exc)
            record_error(self.db, "improve", "submit_failed", str(exc), detail={"card": card_id})

    def _start_cycle(self, trigger: CycleTrigger) -> asyncio.Task[CycleResult]:
        self._cycle = asyncio.create_task(
            run_cycle(
                self.db,
                self.settings,
                self.engine.runtime,
                trigger=trigger,
                submit=self._submit,
                now=self.clock,
            ),
            name=f"prat-improve-{trigger}",
        )
        return self._cycle

    async def tick(self) -> None:
        """One scheduling pass. Never raises."""
        try:
            if not self.cycle_running and may_start(self.db, self.clock()).start:
                self._start_cycle(CycleTrigger.SCHEDULE)
            for card_id in runnable_self_cards(self.db):
                self._submit(card_id)
            # Landing, rework and pending pushes; a trial can take minutes, so it
            # runs beside the loop, one pass at a time.
            if not self.landing_running and load_enabled(self.db):
                self._start_landing()
        except Exception as exc:
            log.exception("self-improvement tick failed")
            record_error(self.db, "improve", type(exc).__name__, str(exc), exc=exc)

    async def run_now(self) -> CycleResult:
        """A manual cycle, started now if none is running. Waits for it to end."""
        if self.cycle_running:
            return CycleResult(None, CycleStatus.SKIPPED, "a cycle is running")
        return await self._start_cycle(CycleTrigger.MANUAL)

    def start_manual(self) -> bool:
        """Start a manual cycle in the background. False if one is already running."""
        if self.cycle_running:
            return False
        self._start_cycle(CycleTrigger.MANUAL)
        return True

    def start(self) -> None:
        recovered = fail_interrupted_cycles(self.db)
        if recovered:
            log.warning("self-improvement cycles interrupted by an earlier stop: %s", recovered)
        if self._loop is None:
            self._loop = asyncio.create_task(self._run_forever(), name="prat-improve-scheduler")

    async def _run_forever(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.tick_seconds)

    async def stop(self) -> None:
        for task in (self._loop, self._cycle, self._landing):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._loop = None
        if self._cycle is not None and self._cycle.cancelled():
            # The server is stopping mid-cycle: the next start marks it failed.
            log.info("self-improvement cycle stopped with the server")
