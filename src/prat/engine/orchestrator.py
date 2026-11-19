"""The run engine.

A run moves through persisted steps (prepare, plan, execute, verify,
critique, summarize, finalize). Each step records its status, attempts and
detail in ``run_steps`` before and after it runs, so a run interrupted by a
crash can be resumed: :meth:`Engine.resume` re-enters the first unfinished
step. Steps are written to be idempotent.

Outcomes are decided by steps and stored on the run; ``finalize`` always runs
and applies the outcome to the card:

=============  ===========
run outcome    card status
=============  ===========
succeeded      review
blocked        blocked
failed         failed
cancelled      cancelled
=============  ===========

Approval (:meth:`Engine.approve`) merges the card branch into the agent
clone's default branch with ``--no-ff``. Nothing is ever pushed.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from prat import governor, memory, onboard, paths, store, swarm
from prat.changes import classify_diff
from prat.config import Settings
from prat.db import Card, Database, Project, Run, RunStep, as_utc
from prat.dolt import versioning
from prat.domain import (
    STEP_ORDER,
    Autonomy,
    CardKind,
    CardStatus,
    CommentAuthor,
    RunStatus,
    StepName,
    StepStatus,
    Verdict,
    lowest_autonomy,
)
from prat.engine import context_pack, crewing, speculative
from prat.engine.instructions import load_repo_instructions
from prat.engine.interfaces import (
    NoPlanner,
    Planner,
    PlanRequest,
    PlanResult,
    Reviewer,
    ReviewRequest,
)
from prat.engine.limits import Limiter
from prat.engine.prompts import (
    builder_system_prompt,
    builder_task_prompt,
    project_facts,
    repository_instructions,
)
from prat.engine.race import Race
from prat.engine.security import SecurityResult, SecurityReviewer, SecurityVerdict
from prat.engine.summary import render_summary
from prat.engine.telemetry import RunTelemetry
from prat.environments import (
    EnvironmentManager,
    checkout_variables,
    database_environ,
    load_local,
)
from prat.errors import record_error
from prat.git import CloneError, Git, GitError, MergeConflict, WorktreeManager, card_branch
from prat.infra import INFRA_TASK, infra_system_prompt
from prat.journal import Journal
from prat.knowledge.provide import for_card
from prat.knowledge.select import Selection
from prat.model_registry import Role, resolve_model
from prat.overlay import load_overlay, overlay_path
from prat.policy import Decision, McpToolSpec, Policy, ToolCall
from prat.projects import (
    PROJECT_FILE_REL,
    ProjectSpec,
    command_argv,
    command_text,
    parse_spec,
    show_project_file,
)
from prat.runtime import (
    ALL_TOOLS,
    READ_TOOLS,
    AgentRuntime,
    AgentTask,
    BlockedEvent,
    Budget,
    CostEvent,
    DoneEvent,
    Outcome,
    RuntimeEvent,
    SessionStarted,
    ToolCallEvent,
    event_to_dict,
)
from prat.runtime.base import PressureEvent
from prat.runtime.loop import LoopDetector
from prat.sandbox import Sandbox, SandboxError, environment_sandboxes
from prat.sandbox import for_settings as sandbox_for
from prat.setup_agent import (
    DATABASE_VARIABLE,
    MYSQL_DATABASE_VARIABLE,
    SETUP_TASK,
    check_environments,
    fallback_database,
    fallback_gate,
    leftover_gate,
    problem_gate,
    proposed_spec,
    setup_system_prompt,
    switch_to_fallback,
)
from prat.verification import GateResult, check_diff, empty_diff_finding, run_gates
from prat.workspace import DatabaseMergeConflict, DoltBranchProvider, WorkspaceError
from prat.workspace.dolt_branch import branch_name, providers_for
from prat.workspace.postgres_copy import is_doltgres

log = logging.getLogger(__name__)

# Added to the builder's instructions when its shell commands run in the sandbox.
SANDBOX_NOTE = """

# Sandbox
Your shell commands run in a fresh container that sees only this worktree, at the same path.
It {network} network access; the project's databases are reachable at the addresses in your
environment. Nothing you start survives the command, and `cd` does not carry over between
commands. The repository's git data is read-only there: read it freely (`git status`,
`git diff`, `git log`, `git show`), but do not stage, commit, stash, switch branches or use
any other git command that writes. This replaces the rule about committing: PRAT commits
everything you leave in the worktree when you finish. To undo a change to a file, write the
old content back (`git show HEAD:path > path`).
"""


def sandbox_gate(problem: str) -> GateResult:
    """A sandbox that cannot run, as a failed gate: the gates are never run without it."""
    return GateResult(
        name="sandbox",
        command="start the sandbox",
        exit_code=None,
        passed=False,
        timed_out=False,
        cancelled=False,
        duration_seconds=0.0,
        output_tail=problem,
        log_path="",
    )


MAX_REVIEW_DIFF_CHARS = 200_000
# The reason on runs and queued cards stopped by a restart; resume_after_restart picks them up.
SUSPENDED_REASON = "PRAT restarted"

STOPPED_REASON = "stopped by a person; the branch and its work are kept"

OUTCOME_TO_CARD = {
    RunStatus.SUCCEEDED: CardStatus.REVIEW,
    RunStatus.BLOCKED: CardStatus.BLOCKED,
    RunStatus.FAILED: CardStatus.FAILED,
    RunStatus.CANCELLED: CardStatus.CANCELLED,
}


class EngineError(RuntimeError):
    pass


class ApproveError(EngineError):
    pass


@dataclass(frozen=True)
class RunResult:
    card_id: int
    run_id: int | None
    run_status: RunStatus | None
    card_status: CardStatus
    reason: str | None


@dataclass(frozen=True)
class MergeResult:
    card_id: int
    branch: str
    commit: str


@dataclass
class StepResult:
    status: StepStatus = StepStatus.SUCCEEDED
    detail: dict[str, Any] = field(default_factory=dict)
    outcome: RunStatus | None = None
    reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class _Evidence:
    """What the latest run proved, and for which commit."""

    verified_commit: str | None
    reviewed: bool
    reviewed_commit: str | None
    verdict: str | None
    require_person: tuple[str, ...]
    security_required: bool = False
    security_verdict: str | None = None
    security_commit: str | None = None
    # Database name -> branch head the gates and reviewers saw.
    database_heads: tuple[tuple[str, str], ...] = ()

    @classmethod
    def of(cls, run: Run | None) -> _Evidence:
        if run is None:
            return cls(None, False, None, None, ())
        steps = {step.name: step for step in run.steps}
        verify, critique = steps.get(StepName.VERIFY), steps.get(StepName.CRITIQUE)
        verified = None
        require: tuple[str, ...] = ()
        heads: tuple[tuple[str, str], ...] = ()
        if verify is not None and verify.status == StepStatus.SUCCEEDED:
            verified = verify.detail.get("head_commit")
            changes = verify.detail.get("changes")
            if isinstance(changes, dict):
                require = tuple(str(name) for name in changes.get("require_person") or ())
            heads = tuple(
                (str(item.get("name")), str(item.get("head")))
                for item in verify.detail.get("databases") or []
                if isinstance(item, dict)
            )
        reviewed = critique is not None and critique.status == StepStatus.SUCCEEDED
        detail = dict(critique.detail or {}) if critique is not None and reviewed else {}
        raw = detail.get("security")
        security: dict[str, Any] = raw if isinstance(raw, dict) else {}
        return cls(
            verified_commit=verified if isinstance(verified, str) else None,
            reviewed=reviewed,
            reviewed_commit=detail.get("head_commit"),
            verdict=detail.get("verdict"),
            require_person=require,
            security_required=bool(detail.get("security_required")),
            security_verdict=security.get("verdict"),
            security_commit=security.get("head_commit"),
            database_heads=heads,
        )

    def check_databases(self, card_id: int, current: dict[str, str]) -> None:
        """Approval is bound to the data the gates and reviewers saw, like the code."""
        for name, verified in self.database_heads:
            now = current.get(name)
            if now != verified:
                raise ApproveError(
                    f"card {card_id}: database {name} moved since it was verified "
                    f"({verified[:10]} then, {(now or 'missing')[:10]} now); run the card again"
                )

    def check(self, card_id: int, branch: str, head: str, *, automatic: bool) -> None:
        again = "run the card again so the current commit is verified and reviewed"
        if self.verified_commit is None:
            raise ApproveError(f"card {card_id} has no verification evidence; {again}")
        if self.verified_commit != head:
            raise ApproveError(
                f"card {card_id}: {branch} is at {head[:10]} but was verified at "
                f"{self.verified_commit[:10]}; {again}"
            )
        if self.reviewed and self.reviewed_commit != head:
            reviewed = (self.reviewed_commit or "an unknown commit")[:10]
            raise ApproveError(
                f"card {card_id}: {branch} is at {head[:10]} but was reviewed at {reviewed}; "
                f"{again}"
            )
        if self.security_commit is not None and self.security_commit != head:
            raise ApproveError(
                f"card {card_id}: {branch} is at {head[:10]} but its security review was for "
                f"{self.security_commit[:10]}; {again}"
            )
        if not automatic:
            return
        if not self.reviewed or self.verdict != str(Verdict.MET):
            raise ApproveError(
                f"card {card_id}: automatic approval needs a 'met' verdict for {head[:10]}"
            )
        if self.security_required and self.security_verdict != str(SecurityVerdict.PASS):
            raise ApproveError(
                f"card {card_id}: automatic approval needs a security review pass for {head[:10]}"
            )
        if self.require_person:
            raise ApproveError(
                f"card {card_id} needs a person's approval: {', '.join(self.require_person)}"
            )


@dataclass
class _Active:
    run_id: int
    card_id: int
    task: asyncio.Task[Any] | None
    stop: threading.Event = field(default_factory=threading.Event)
    session_id: str | None = None
    cancel_requested: bool = False
    finalizing: bool = False
    # A review started while the gates run, for the commit it reviews (see _step_verify).
    early_review: tuple[str, asyncio.Task[Any]] | None = None
    # Speculative reviews, one per builder's worktree while racing: (tree, review).
    speculative: dict[str, tuple[str, asyncio.Task[Any]]] = field(default_factory=dict)


@dataclass
class _Ctx:
    """Everything a step needs, rebuilt from the database on (re)entry."""

    run_id: int
    card_id: int
    project_id: int
    project_slug: str
    checkout_path: Path
    agent_path: Path
    default_branch: str
    title: str
    goal: str
    acceptance_criteria: str
    complexity: str
    card_autonomy: Autonomy | None
    feedback: str | None
    budget_usd: float | None
    session_id: str | None
    worktree: Path | None
    branch: str | None
    base_commit: str | None
    start_commit: str | None
    spec: ProjectSpec | None
    autonomy: Autonomy | None
    journal: Journal
    run_dir: Path
    plan: str | None = None
    # A setup or infra card's agent proposes the project file and its proposal is what verify
    # proves, environments included.
    card_kind: CardKind = CardKind.CHANGE

    @property
    def is_setup(self) -> bool:
        return self.card_kind in (CardKind.SETUP, CardKind.INFRA)

    # Accessors for values that exist once the prepare step has run.

    @property
    def worktree_path(self) -> Path:
        return _need(self.worktree, "worktree")

    @property
    def project_spec(self) -> ProjectSpec:
        return _need(self.spec, "project spec")

    @property
    def run_autonomy(self) -> Autonomy:
        return _need(self.autonomy, "autonomy")

    @property
    def base(self) -> str:
        return _need(self.base_commit, "base commit")

    @property
    def start(self) -> str:
        return _need(self.start_commit, "start commit")


class Engine:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        runtime: AgentRuntime,
        *,
        reviewer: Reviewer | None = None,
        planner: Planner | None = None,
        mcp_tools: Iterable[McpToolSpec] = (),
        security_reviewer: SecurityReviewer | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.runtime = runtime
        self.reviewer = reviewer
        # Used for projects that ask for it and always for PRAT's own repository.
        self.security_reviewer = security_reviewer
        self.planner: Planner = planner or NoPlanner()
        self.mcp_tools = list(mcp_tools)
        self.owner = uuid.uuid4().hex
        self.worktrees = WorktreeManager(
            settings.worktrees_dir,
            author_name=settings.git_author_name,
            author_email=settings.git_author_email,
        )
        self.limiter = Limiter(settings.max_concurrent_runs, settings.max_concurrent_per_project)
        self.governor = governor.Governor(settings, db)
        self._start_lock = asyncio.Lock()
        self._project_locks: dict[int, asyncio.Lock] = {}
        self._active: dict[int, _Active] = {}
        # Set by suspend(): a cancelled task is a restart, not a person cancelling.
        self._suspending = False
        self._tasks: dict[int, asyncio.Task[RunResult]] = {}
        # Lessons being written in the background, so none is lost to the garbage collector.
        self._lessons: set[asyncio.Task[Any]] = set()
        # Usage and live state per running run (prat.engine.telemetry).
        self._telemetry: dict[int, RunTelemetry] = {}
        # Setups of the checkout approved work reached, likewise.
        self._setups: set[asyncio.Task[Any]] = set()
        # Crew members choosing their names in the background (see _introductions).
        self._introducing: asyncio.Task[None] | None = None
        settings.ensure_dirs()

    # Queue -------------------------------------------------------------------

    def submit(self, card_id: int) -> asyncio.Task[RunResult]:
        """Queue a card. The returned task finishes when the run does."""
        if card_id in self._tasks and not self._tasks[card_id].done():
            raise EngineError(f"card {card_id} is already queued or running")
        task = asyncio.create_task(self.run_card(card_id), name=f"prat-card-{card_id}")
        self._tasks[card_id] = task
        task.add_done_callback(functools.partial(self._forget, card_id))
        return task

    def submit_resume(self, card_id: int, run_id: int) -> asyncio.Task[RunResult]:
        """Resume an interrupted run of ``card_id`` in the background."""
        if self.is_scheduled(card_id):
            raise EngineError(f"card {card_id} is already queued or running")
        task = asyncio.create_task(self.resume(run_id), name=f"prat-resume-{run_id}")
        self._tasks[card_id] = task
        task.add_done_callback(functools.partial(self._forget, card_id))
        return task

    async def suspend(self) -> None:
        """Stop for a restart. A running card's run is left interrupted (its worktree, agent
        session and database branch kept) and a queued card stays ready; both are picked up
        again by :meth:`resume_after_restart`. Cancelling instead would end the run and drop
        its data branch."""
        self._suspending = True
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def resume_after_restart(self, recovered: Iterable[int] = ()) -> list[int]:
        """Resume runs a restart interrupted (and ``recovered``, runs a crash left behind) and
        queue cards that were waiting. Returns the card ids picked up."""
        picked: list[int] = []
        with self.db.session() as session:
            interrupted = list(
                session.scalars(
                    select(Run).where(
                        Run.status == RunStatus.INTERRUPTED,
                        (Run.reason == SUSPENDED_REASON) | Run.id.in_(list(recovered)),
                    )
                )
            )
            waiting = list(
                session.scalars(select(Card).where(Card.status_reason == SUSPENDED_REASON))
            )
            resumes = [(run.card_id, run.id) for run in interrupted]
            queued = [
                card.id for card in waiting if card.status in (CardStatus.READY, CardStatus.REWORK)
            ]
        for card_id, run_id in resumes:
            if not self.is_scheduled(card_id):
                self.submit_resume(card_id, run_id)
                picked.append(card_id)
        for card_id in queued:
            if not self.is_scheduled(card_id):
                self.submit(card_id)
                picked.append(card_id)
        return picked

    def is_scheduled(self, card_id: int) -> bool:
        """True while a card is queued or running in this engine."""
        task = self._tasks.get(card_id)
        return task is not None and not task.done()

    def scheduled_cards(self) -> frozenset[int]:
        return frozenset(card_id for card_id in list(self._tasks) if self.is_scheduled(card_id))

    def is_active(self, card_id: int) -> bool:
        """True while a run of the card is past the queue and executing steps."""
        return card_id in self._active

    def _forget(self, card_id: int, task: asyncio.Task[RunResult]) -> None:
        if self._tasks.get(card_id) is task:
            del self._tasks[card_id]

    async def run_card(self, card_id: int) -> RunResult:
        """Run a ready (or rework) card through every step, respecting limits."""
        with self.db.session() as session:
            card = store.get_card(session, card_id)
            if card.status not in (CardStatus.READY, CardStatus.REWORK):
                raise EngineError(
                    f"card {card_id} is {card.status}; only ready or rework cards run"
                )
            project_id = card.project_id
        try:
            while True:
                await self._governed(card_id)
                async with self.limiter.slot(project_id):
                    # Checked again with the slot held and no other start in between, so two
                    # cards that collide never both pass.
                    async with self._start_lock:
                        reason = await asyncio.to_thread(self.governor.check, card_id)
                        if reason is None:
                            self._hold(card_id, None)
                            run_id = self._start_run(card_id)
                    if reason is None:
                        return await self._drive(run_id, card_id)
                self._hold(card_id, reason)
        except asyncio.CancelledError:
            # Cancelled while still queued: no run exists yet.
            if self._suspending:
                with self.db.session() as session:
                    card = store.get_card(session, card_id)
                    if card.status in (CardStatus.READY, CardStatus.REWORK):
                        card.status_reason = SUSPENDED_REASON  # queued again after the restart
                raise
            with self.db.session() as session:
                card = store.get_card(session, card_id)
                if card.status in (CardStatus.READY, CardStatus.REWORK):
                    store.set_card_status(session, card, CardStatus.CANCELLED, "cancelled in queue")
                    return RunResult(
                        card_id, None, None, CardStatus.CANCELLED, "cancelled in queue"
                    )
            raise

    async def _governed(self, card_id: int) -> None:
        """Wait while the governor holds the card back, saying why on the card."""
        while True:
            reason = await asyncio.to_thread(self.governor.check, card_id)
            self._hold(card_id, reason)
            if reason is None:
                return
            await asyncio.sleep(self.settings.governor_poll_seconds)

    def _hold(self, card_id: int, reason: str | None) -> None:
        with self.db.session() as session:
            card = session.get(Card, card_id)
            if card is None or card.hold_reason == reason:
                return
            card.hold_reason = reason
            if reason is not None:
                store.record_event(
                    session,
                    "card.held",
                    project_id=card.project_id,
                    card_id=card_id,
                    data={"reason": reason},
                )

    async def resume(self, run_id: int) -> RunResult:
        """Continue an interrupted run at its first unfinished step."""
        with self.db.session() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise EngineError(f"no run {run_id}")
            if run.status == RunStatus.RUNNING and self._is_stale(run):
                self._interrupt(session, run)
            if run.status != RunStatus.INTERRUPTED:
                raise EngineError(f"run {run_id} is {run.status}; only interrupted runs resume")
            card_id = run.card_id
            project_id = run.card.project_id
        async with self.limiter.slot(project_id):
            with self.db.session() as session:
                run = _get_run(session, run_id)
                store.set_run_status(session, run, RunStatus.RUNNING, "resumed")
                run.owner = self.owner
                run.heartbeat_at = _now()
            self._journal_for(run_id).append("run.resumed", {"owner": self.owner})
            return await self._drive(run_id, card_id)

    def recover(self) -> list[int]:
        """Mark runs abandoned by a dead process as interrupted. Returns their ids."""
        recovered: list[int] = []
        with self.db.session() as session:
            runs = session.scalars(select(Run).where(Run.status == RunStatus.RUNNING)).all()
            for run in runs:
                if run.card_id in self._active or not self._is_stale(run):
                    continue
                self._interrupt(session, run)
                recovered.append(run.id)
        return recovered

    def _is_stale(self, run: Run) -> bool:
        if run.owner == self.owner and run.card_id in self._active:
            return False
        beat = as_utc(run.heartbeat_at)
        if beat is None:
            return True
        return _now() - beat >= timedelta(seconds=self.settings.stale_run_seconds)

    @staticmethod
    def _interrupt(session: Any, run: Run) -> None:
        store.set_run_status(session, run, RunStatus.INTERRUPTED, "process stopped mid-run")
        for step in run.steps:
            if step.status == StepStatus.RUNNING:
                step.status = StepStatus.PENDING

    async def stop(self, card_id: int) -> bool:
        """Stop the card's current run and keep the card.

        The run ends cancelled and the card goes to failed, with its branch, its work
        (saved by the safety commit) and its database branches kept, so it can be run
        again or sent back with feedback. Returns ``True`` if a run was stopped.
        """
        active = self._active.get(card_id)
        if active is not None:
            if active.finalizing:
                return False
            with self.db.session() as session:
                _get_run(session, active.run_id).stop_only = True
            await self._cancel_active(active)
            return True
        with self.db.session() as session:
            card = store.get_card(session, card_id)
            run = store.latest_run(session, card)
            if card.status != CardStatus.RUNNING or run is None:
                return False
            if run.status == RunStatus.RUNNING and not self._is_stale(run):
                # Owned by another live process: ask it to stop via the database.
                run.stop_only = True
                run.cancel_requested_at = _now()
                store.record_event(session, "run.stop_requested", card_id=card_id, run_id=run.id)
                return True
            if run.status not in (RunStatus.RUNNING, RunStatus.INTERRUPTED):
                return False
            if run.status == RunStatus.RUNNING:
                self._interrupt(session, run)
            store.set_run_status(session, run, RunStatus.CANCELLED, STOPPED_REASON)
            run.stop_only = True
            run.finished_at = _now()
            store.set_card_status(session, card, CardStatus.FAILED, STOPPED_REASON)
            worktree = Path(run.worktree_path) if run.worktree_path else None
            agent = Path(card.project.agent_path)
            run_id = run.id
        if worktree is not None and worktree.exists():
            await asyncio.to_thread(
                self.worktrees.safety_commit,
                worktree,
                f"PRAT: save work from run {run_id} (card {card_id}), stopped",
            )
            await asyncio.to_thread(self.worktrees.remove_worktree, agent, worktree)
        versioning.commit(self.db, f"card {card_id}: run stopped", actor="person")
        return True

    async def cancel(self, card_id: int) -> bool:
        """Cancel a queued or running card. Returns ``True`` if anything changed."""
        changed = await self._cancel(card_id)
        if changed:
            await self._drop_databases(card_id)
            versioning.commit(self.db, f"card {card_id} cancelled", actor="person")
        return changed

    async def _drop_databases(self, card_id: int) -> None:
        """A cancelled card's database branches go with it. Best effort."""
        with self.db.session() as session:
            card = store.get_card(session, card_id)
            run = store.latest_run(session, card)
            spec = (
                ProjectSpec.model_validate(run.spec) if run and run.spec else _spec_of(card.project)
            )
            environ = self._environ(card.project.slug)
        try:
            for project_db in _databases(spec, environ):
                await asyncio.to_thread(project_db.discard, card_id)
        except (WorkspaceError, SQLAlchemyError) as exc:
            log.warning("could not drop the database branches of card %s: %s", card_id, exc)

    async def _cancel(self, card_id: int) -> bool:
        active = self._active.get(card_id)
        if active is not None:
            if active.finalizing:
                return False
            await self._cancel_active(active)
            return True
        task = self._tasks.get(card_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        with self.db.session() as session:
            card = store.get_card(session, card_id)
            if card.status == CardStatus.RUNNING:
                run = store.latest_run(session, card)
                if run is not None and run.status == RunStatus.RUNNING and not self._is_stale(run):
                    # Owned by another live process: ask it to stop via the database.
                    run.cancel_requested_at = _now()
                    store.record_event(
                        session, "run.cancel_requested", card_id=card_id, run_id=run.id
                    )
                    return True
                if run is not None and run.status in (RunStatus.RUNNING, RunStatus.INTERRUPTED):
                    if run.status == RunStatus.RUNNING:
                        self._interrupt(session, run)
                    store.set_run_status(session, run, RunStatus.CANCELLED, "cancelled")
                    run.finished_at = _now()
                    worktree = run.worktree_path
                    agent = Path(card.project.agent_path)
                    store.set_card_status(session, card, CardStatus.CANCELLED, "cancelled")
                    if worktree:
                        await asyncio.to_thread(
                            self.worktrees.remove_worktree, agent, Path(worktree)
                        )
                    return True
            if card.status in (CardStatus.DONE, CardStatus.CANCELLED):
                return False
            store.set_card_status(session, card, CardStatus.CANCELLED, "cancelled")
            return True

    async def _cancel_active(self, active: _Active) -> None:
        if active.cancel_requested:
            return
        active.cancel_requested = True
        active.stop.set()
        if active.session_id:
            await self.runtime.cancel(active.session_id)
        if active.task is not None and not active.task.done():
            active.task.cancel()

    # Approval and rework ----------------------------------------------------

    async def approve(self, card_id: int, *, automatic: bool = False) -> MergeResult:
        """Merge a reviewed card into the agent clone's default branch, locally.

        Approval is bound to a commit: it is refused when the card branch has
        moved since the gates and the reviewer looked at it. ``automatic``
        (landing without a person) also needs a "met" verdict for that commit
        and no change category the project reserves for a person.
        """
        with self.db.session() as session:
            card = store.get_card(session, card_id)
            if card.status != CardStatus.REVIEW:
                raise ApproveError(f"card {card_id} is {card.status}; only cards in review merge")
            epic = card.kind is CardKind.EPIC
            # A swarm epic whose parts are all merged into its branch lands like a card.
            built = swarm.is_swarm_epic(card)
        if epic and built:
            return await self._land_epic(card_id)
        if epic:
            # Approving an epic approves its split: nothing merges, its parts become ready.
            return await self._approve_epic(card_id)
        with self.db.session() as session:
            card = store.get_card(session, card_id)
            if not card.branch:
                raise ApproveError(f"card {card_id} has no branch")
            project = card.project
            agent = Path(project.agent_path)
            checkout = Path(project.checkout_path)
            branch = card.branch
            default = project.default_branch
            title = card.title
            run = store.latest_run(session, card)
            spec = ProjectSpec.model_validate(run.spec) if run and run.spec else _spec_of(project)
            evidence = _Evidence.of(run)
        async with self._project_lock(project.id):
            head = await asyncio.to_thread(Git(agent).rev_parse, f"refs/heads/{branch}")
            evidence.check(card_id, branch, head, automatic=automatic)
            base = await asyncio.to_thread(self.worktrees.merge_base, agent, default, branch)
            stats = await asyncio.to_thread(self.worktrees.diff_stats, agent, base, branch)
            if stats.changed_lines > spec.max_diff_lines or len(stats.files) > spec.max_diff_files:
                raise ApproveError(
                    f"card {card_id} diff is over the project limits "
                    f"({len(stats.files)} files, {stats.changed_lines} lines)"
                )
            try:
                project_dbs = _databases(spec, self._environ(project.slug))
            except WorkspaceError as exc:
                raise ApproveError(f"card {card_id}: {exc}") from exc
            current = {
                project_db.spec.name: await asyncio.to_thread(project_db.head, card_id)
                for project_db in project_dbs
            }
            evidence.check_databases(card_id, current)
            # Data first, then code; if either fails, nothing is kept.
            merged_data: list[tuple[DoltBranchProvider, str, str]] = []
            try:
                for project_db in project_dbs:
                    before, after = await asyncio.to_thread(
                        project_db.merge, card_id, f"Merge card {card_id}: {title}"
                    )
                    merged_data.append((project_db, before, after))
            except (DatabaseMergeConflict, WorkspaceError, SQLAlchemyError) as exc:
                await _undo_data(merged_data)
                with self.db.session() as session:
                    store.add_comment(
                        session,
                        store.get_card(session, card_id),
                        f"Database merge failed: {exc}",
                        author=CommentAuthor.SYSTEM,
                        kind="merge_conflict",
                    )
                raise ApproveError(f"card {card_id}: database merge failed: {exc}") from exc
            try:
                commit = await asyncio.to_thread(
                    self.worktrees.merge, agent, branch, default, f"Merge card {card_id}: {title}"
                )
            except MergeConflict as exc:
                # The code did not land, so the data must not either.
                await _undo_data(merged_data)
                with self.db.session() as session:
                    store.add_comment(
                        session,
                        store.get_card(session, card_id),
                        f"Merge failed with conflicts in: {', '.join(exc.files)}",
                        author=CommentAuthor.SYSTEM,
                        kind="merge_conflict",
                        data={"files": exc.files},
                    )
                raise ApproveError(str(exc)) from exc
            except BaseException:
                await _undo_data(merged_data)
                raise
            followed = await asyncio.to_thread(
                _follow_checkout, checkout, agent, default, onboard.projects_dir(self.settings)
            )
        # A checkpoint: the git merge and each database merge, landed together.
        data_merges = [
            {"name": db.spec.name, "database": db.database, "before": before, "merge": after}
            for db, before, after in merged_data
        ]
        with self.db.session() as session:
            card = store.get_card(session, card_id)
            store.set_card_status(session, card, CardStatus.DONE, f"merged as {commit[:10]}")
            store.record_event(
                session,
                "card.merged",
                project_id=card.project_id,
                card_id=card_id,
                data={
                    "commit": commit,
                    "branch": branch,
                    "branch_head": head,
                    "automatic": automatic,
                    "databases": data_merges,
                    "checkout": followed,
                },
            )
        # The record commit names the code and data it goes with: all three stay paired.
        actor = "engine (automatic)" if automatic else "person"
        data_note = "".join(f", data {m['database']}@{m['merge']}" for m in data_merges)
        if versioning.commit(
            self.db,
            f"card {card_id} approved: merged {branch} as git {commit}{data_note}",
            actor=actor,
        ):
            versioning.tag(self.db, f"card-{card_id}-approved", message=f"git {commit}{data_note}")
        if followed == "updated":
            self._start_checkout_setup(project.slug, card_id)
        with self.db.session() as session:
            runs = len(store.get_card(session, card_id).runs)
        self._settle(project.slug, card_id, "landed", first_review=runs == 1)
        await asyncio.to_thread(self._epic_progress, card_id)
        await asyncio.to_thread(self._promote_setup_skills, project.slug, card_id)
        await asyncio.to_thread(self._idea_landed, card_id)
        return MergeResult(card_id, branch, commit)

    def _idea_landed(self, card_id: int) -> None:
        """A card that put an accepted idea in place landed (prat.ideas)."""
        from prat import ideas

        try:
            ideas.card_landed(self.settings.home, card_id)
        except Exception:
            log.exception("could not mark the idea of card %s in place", card_id)

    def _promote_setup_skills(self, slug: str, card_id: int) -> None:
        """Approving a setup card approves the skills its agent proposed (the project's
        vocabulary and layout): the person reviewed the whole proposal in one go."""
        from prat import skills

        with self.db.session() as session:
            if store.get_card(session, card_id).kind is not CardKind.SETUP:
                return
        for candidate in skills.candidates(self.settings.home, slug):
            if f"card {card_id} " in f"{candidate.source} ":
                with contextlib.suppress(skills.SkillError):
                    skills.promote(self.settings.home, slug, candidate.name)

    def _settle(
        self,
        slug: str,
        card_id: int,
        outcome: str,
        *,
        feedback: str = "",
        first_review: bool = False,
    ) -> None:
        """Tell every agent that worked on the card how it turned out (prat.memory)."""
        try:
            memory.settle(
                self.settings.home,
                project=slug,
                card_id=card_id,
                outcome=outcome,
                feedback=feedback,
                first_review=first_review,
            )
        except Exception:
            log.exception("could not settle the memories of card %s", card_id)

    def _start_checkout_setup(self, slug: str, card_id: int) -> None:
        """Set up the checkout the approved work just reached (its dependencies and built
        files), in the background: it never holds up or undoes the approval. Without it the
        person runs the setup by hand, against the default branch of the database."""
        from prat.infra import set_up_person_checkout

        async def set_up() -> None:
            problem = await asyncio.to_thread(set_up_person_checkout, self.settings, self.db, slug)
            with self.db.session() as session:
                card = store.get_card(session, card_id)
                store.record_event(
                    session,
                    "checkout.setup",
                    project_id=card.project_id,
                    card_id=card_id,
                    data={"problem": problem},
                )
                if problem is not None:
                    store.add_comment(
                        session,
                        card,
                        f"The approved work is in the checkout, but its setup failed: {problem}",
                        author=CommentAuthor.SYSTEM,
                        kind="checkout_setup",
                    )

        task = asyncio.create_task(set_up(), name=f"prat-checkout-setup-{card_id}")
        self._setups.add(task)
        task.add_done_callback(self._setups.discard)

    async def checkouts_set_up(self) -> None:
        """Wait for checkout setups still running (used by commands that exit after approving)."""
        if self._setups:
            await asyncio.gather(*self._setups, return_exceptions=True)

    def rework(self, card_id: int, feedback: str | None = None) -> None:
        """Send a card back for another builder run on the same branch.

        ``feedback`` is stored as a human comment. Without it, a human comment
        must already have been added since the last run. The next run is given
        the new human comments and the previous reviewer verdict.
        """
        with self.db.session() as session:
            card = store.get_card(session, card_id)
            if feedback is not None and feedback.strip():
                store.add_comment(
                    session, card, feedback, author=CommentAuthor.HUMAN, kind="feedback"
                )
                session.flush()
            elif not store.human_comments_since_last_run(session, card):
                raise EngineError(
                    "rework needs feedback: add a comment to the card first or pass feedback"
                )
            if card.status != CardStatus.REWORK:
                # Already waiting for rework: the new feedback joins what the next run reads.
                store.set_card_status(session, card, CardStatus.REWORK, "sent back for rework")
            slug = card.project.slug
            said = feedback or "\n".join(
                c.body for c in store.human_comments_since_last_run(session, card)
            )
        versioning.commit(self.db, f"card {card_id} sent back for rework", actor="person")
        self._settle(slug, card_id, "reworked", feedback=said or "")

    # Run driving --------------------------------------------------------------

    def _start_run(self, card_id: int) -> int:
        with self.db.session() as session:
            card = store.get_card(session, card_id)
            feedback = (
                store.rework_feedback(session, card) if card.status == CardStatus.REWORK else None
            )
            run = Run(
                card=card,
                attempt=len(card.runs) + 1,
                status=RunStatus.QUEUED,
                runtime=self.runtime.name,
                budget_usd=card.budget_usd or self.settings.default_budget_usd,
                feedback=feedback,
                owner=self.owner,
            )
            session.add(run)
            session.flush()
            run.journal_path = str(self.settings.journal_dir / f"run-{run.id}.jsonl")
            store.set_run_status(session, run, RunStatus.RUNNING)
            run.started_at = _now()
            run.heartbeat_at = _now()
            for position, name in enumerate(STEP_ORDER):
                session.add(
                    RunStep(
                        run=run, name=name, position=position, status=StepStatus.PENDING, detail={}
                    )
                )
            store.set_card_status(session, card, CardStatus.RUNNING)
            run_id = run.id
        self._journal_for(run_id).append("run.started", {"card_id": card_id, "owner": self.owner})
        return run_id

    async def _drive(self, run_id: int, card_id: int) -> RunResult:
        active = _Active(run_id=run_id, card_id=card_id, task=asyncio.current_task())
        self._active[card_id] = active
        heartbeat = asyncio.create_task(self._heartbeat(active), name=f"prat-heartbeat-{run_id}")
        try:
            await self._run_steps(active)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            self._active.pop(card_id, None)
            telemetry = self._telemetry.pop(run_id, None)
            if telemetry is not None and telemetry.flush():
                self._update_run(run_id, usage=telemetry.usage(), live=dict(telemetry.live))
        result = self._result(run_id)
        ended = {
            CardStatus.FAILED: "failed",
            CardStatus.BLOCKED: "blocked",
            CardStatus.CANCELLED: "cancelled",
        }.get(result.card_status)
        if ended is not None:
            with self.db.session() as session:
                slug = store.get_card(session, card_id).project.slug
            self._settle(slug, card_id, ended, feedback=result.reason or "")
        if result.card_status == CardStatus.REVIEW and self.settings.lessons == "review":
            self._start_lesson(run_id)
        return result

    def _start_lesson(self, run_id: int) -> None:
        """Write the run's lesson in the background: it never holds up or fails the run."""
        from prat.lessons import teach

        ctx = self._context(run_id)
        task = asyncio.create_task(
            teach(
                self.db,
                self.settings.home,
                self.runtime,
                run_id,
                model=self._model(Role.TEACHER, ctx),
            ),
            name=f"prat-lesson-{run_id}",
        )
        self._lessons.add(task)
        task.add_done_callback(self._lessons.discard)

    async def lessons_written(self) -> None:
        """Wait for the lessons still being written (used by commands that exit after a run)."""
        if self._lessons:
            await asyncio.gather(*self._lessons, return_exceptions=True)

    async def _heartbeat(self, active: _Active) -> None:
        while True:
            await asyncio.sleep(self.settings.heartbeat_seconds)
            with self.db.session() as session:
                run = session.get(Run, active.run_id)
                if run is None:
                    return
                run.heartbeat_at = _now()
                requested = run.cancel_requested_at is not None
            if requested and not active.finalizing:
                await self._cancel_active(active)

    async def _run_steps(self, active: _Active) -> None:
        handlers: dict[StepName, Callable[[_Ctx, _Active], Awaitable[StepResult]]] = {
            StepName.PREPARE: self._step_prepare,
            StepName.PLAN: self._step_plan,
            StepName.EXECUTE: self._step_execute,
            StepName.VERIFY: self._step_verify,
            StepName.CRITIQUE: self._step_critique,
            StepName.SUMMARIZE: self._step_summarize,
            StepName.FINALIZE: self._step_finalize,
        }
        for name in STEP_ORDER:
            with self.db.session() as session:
                step = self._step_row(session, active.run_id, name)
                run = step.run
                if step.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED):
                    continue
                outcome = run.outcome
                if name == StepName.FINALIZE:
                    active.finalizing = True
                    if outcome is None:
                        run.outcome = RunStatus.SUCCEEDED
                elif outcome is not None:
                    if step.status == StepStatus.FAILED:
                        step.status = StepStatus.RUNNING
                    step.status = StepStatus.SKIPPED
                    step.detail = {"reason": f"run outcome is {outcome}"}
                    continue
                step.status = StepStatus.RUNNING
                step.attempts += 1
                step.started_at = _now()
                step.error = None
            ctx = self._context(active.run_id)
            ctx.journal.append("step.started", {"step": str(name)})
            try:
                result = await handlers[name](ctx, active)
            except asyncio.CancelledError:
                if self._suspending:
                    # A restart: leave the run interrupted where it stands, to be resumed.
                    with self.db.session() as session:
                        run = _get_run(session, active.run_id)
                        store.set_run_status(session, run, RunStatus.INTERRUPTED, SUSPENDED_REASON)
                        for row in run.steps:
                            if row.status == StepStatus.RUNNING:
                                row.status = StepStatus.PENDING
                    raise
                current = asyncio.current_task()
                if current is not None:
                    current.uncancel()
                active.cancel_requested = True
                result = StepResult(
                    StepStatus.FAILED,
                    error="cancelled",
                    outcome=RunStatus.CANCELLED,
                    reason="cancelled by request",
                )
            except Exception as exc:
                log.exception("step %s of run %s failed", name, active.run_id)
                record_error(
                    self.db,
                    "engine",
                    type(exc).__name__,
                    str(exc),
                    detail={"step": str(name), "run_id": active.run_id, "card_id": active.card_id},
                    exc=exc,
                )
                result = StepResult(
                    StepStatus.FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                    outcome=RunStatus.FAILED,
                    reason=f"{name} failed: {exc}",
                )
            if name == StepName.FINALIZE and result.status == StepStatus.FAILED:
                # Finalize must still leave the card in a decided state.
                self._apply_outcome(active.run_id)
            self._finish_step(active.run_id, name, result)
            ctx.journal.append(
                "step.finished",
                {
                    "step": str(name),
                    "status": str(result.status),
                    "error": result.error,
                    "outcome": str(result.outcome) if result.outcome else None,
                    "reason": result.reason,
                },
            )

    def _finish_step(self, run_id: int, name: StepName, result: StepResult) -> None:
        with self.db.session() as session:
            step = self._step_row(session, run_id, name)
            if result.status == StepStatus.FAILED and result.outcome is None:
                result.outcome = RunStatus.FAILED
                result.reason = result.reason or f"{name} failed: {result.error}"
            step.status = result.status
            step.detail = result.detail
            step.error = result.error
            step.finished_at = _now()
            run = step.run
            # The first decided outcome wins; later steps never override it.
            if (
                result.outcome is not None
                and name != StepName.FINALIZE
                and (run.outcome is None or run.outcome == RunStatus.SUCCEEDED)
            ):
                run.outcome = result.outcome
                run.reason = result.reason
            card_id = run.card_id
        # On the Dolt store each finished step is a commit of PRAT's records.
        versioning.commit(
            self.db, f"run {run_id} card {card_id} step {name}: {result.status}", actor="engine"
        )

    @staticmethod
    def _step_row(session: Any, run_id: int, name: StepName) -> RunStep:
        step = session.scalars(
            select(RunStep).where(RunStep.run_id == run_id, RunStep.name == name)
        ).one()
        if not isinstance(step, RunStep):
            raise EngineError(f"run {run_id} has no {name} step")
        return step

    def _context(self, run_id: int) -> _Ctx:
        with self.db.session() as session:
            run = _get_run(session, run_id)
            card = run.card
            project = card.project
            return _Ctx(
                run_id=run.id,
                card_id=card.id,
                project_id=project.id,
                project_slug=project.slug,
                checkout_path=Path(project.checkout_path),
                agent_path=Path(project.agent_path),
                default_branch=project.default_branch,
                title=card.title,
                goal=card.goal,
                acceptance_criteria=card.acceptance_criteria,
                complexity=str(card.complexity),
                card_autonomy=card.autonomy,
                feedback=run.feedback,
                budget_usd=run.budget_usd,
                session_id=run.session_id,
                worktree=Path(run.worktree_path) if run.worktree_path else None,
                branch=run.branch,
                base_commit=run.base_commit,
                start_commit=run.start_commit,
                spec=ProjectSpec.model_validate(run.spec) if run.spec else None,
                autonomy=run.autonomy,
                journal=self._journal_for(run.id),
                run_dir=self.settings.run_dir(run.id),
                plan=_plan_from(run),
                card_kind=CardKind(card.kind),
            )

    async def _try_fallback(
        self,
        ctx: _Ctx,
        spec: ProjectSpec,
        active: _Active,
        gate_seconds: float,
        failed: list[GateResult],
    ) -> tuple[list[GateResult], dict[str, str]] | None:
        """A setup proposal that fails on Doltgres is tried on plain PostgreSQL (when this
        machine has a fallback server). If every gate passes there, the project moves to it
        for good; otherwise the failure is not the database's and nothing changes."""
        current = load_local(self.settings.home, ctx.project_slug).variables.get(
            DATABASE_VARIABLE, ""
        )
        if not current or not await asyncio.to_thread(is_doltgres, current):
            return None
        try:
            address = await asyncio.to_thread(fallback_database, self.settings, ctx.project_slug)
        except Exception as exc:
            ctx.journal.append("verify.fallback", {"error": str(exc)})
            return None
        if address is None:
            return None
        env = checkout_variables(
            self.settings.home,
            ctx.project_slug,
            list(spec.workspace.database),
            overrides={DATABASE_VARIABLE: address},
        )
        await asyncio.to_thread(_reset_worktree, ctx.worktree_path)
        try:
            sandbox = await asyncio.to_thread(self._sandbox, ctx, env, spec)
        except SandboxError as exc:
            ctx.journal.append("verify.fallback", {"error": f"sandbox: {exc}"})
            return None
        try:
            gates = await asyncio.to_thread(
                functools.partial(
                    run_gates,
                    spec,
                    ctx.worktree_path,
                    ctx.run_dir,
                    stop=active.stop,
                    timeout=gate_seconds,
                    env=env,
                    sandbox=sandbox,
                )
            )
        finally:
            if sandbox is not None:
                await asyncio.to_thread(sandbox.cleanup)
        ctx.journal.append(
            "verify.fallback", {"passed": all(g.passed for g in gates), "gates": len(gates)}
        )
        if not all(g.passed for g in gates):
            return None
        reason = f"{failed[0].name} gate failed on Doltgres; passed on PostgreSQL"
        await asyncio.to_thread(
            switch_to_fallback, self.settings, ctx.project_slug, address, reason
        )
        return [fallback_gate(failed), *gates], env

    def _environ(self, slug: str) -> dict[str, str]:
        """Where a project's database URL variables are looked up: PRAT's environment with
        this machine's variables for the project."""
        return database_environ(self.settings.home, slug)

    def _journal_for(self, run_id: int) -> Journal:
        return Journal(
            self.settings.journal_dir / f"run-{run_id}.jsonl",
            payload_limit=self.settings.journal_payload_limit,
        )

    def _project_lock(self, project_id: int) -> asyncio.Lock:
        return self._project_locks.setdefault(project_id, asyncio.Lock())

    def _update_run(self, run_id: int, **values: Any) -> None:
        with self.db.session() as session:
            run = _get_run(session, run_id)
            for key, value in values.items():
                setattr(run, key, value)

    def _result(self, run_id: int) -> RunResult:
        with self.db.session() as session:
            run = _get_run(session, run_id)
            return RunResult(run.card_id, run.id, run.status, run.card.status, run.reason)

    # Steps --------------------------------------------------------------------

    async def _step_prepare(self, ctx: _Ctx, active: _Active) -> StepResult:
        with self.db.session() as session:
            all_checkouts = [Path(p.checkout_path) for p in store.list_projects(session)]
            project = store.get_project(session, ctx.project_id)
            fallback_spec = _spec_of(project)
            is_self = project.is_self
            author = project.client.commit_author if project.client else None
            card = store.get_card(session, ctx.card_id)
            branch = card.branch or card_branch(card.id, card.title)
            # A swarm part starts from (and is merged into) its epic's branch (prat.swarm).
            start_ref = card.base_branch or ctx.default_branch
        async with self._project_lock(ctx.project_id):
            if is_self:
                # PRAT's own clone is both checkout and agent clone (prat.improve).
                clone = await asyncio.to_thread(
                    self.worktrees.refresh_agent_clone,
                    ctx.agent_path,
                    ctx.default_branch,
                    source="the self repository's remote",
                    merge_diverged=False,
                )
            else:
                clone = await asyncio.to_thread(
                    self.worktrees.prepare_agent_clone,
                    ctx.checkout_path,
                    ctx.agent_path,
                    ctx.default_branch,
                    author=author,
                )
            spec = _spec_from_branch(ctx.agent_path, ctx.default_branch) or fallback_spec
            # The installation's overlay only adds to the project's rules; the run
            # records the result, so a resumed run keeps the rules it started with.
            overlay = load_overlay(overlay_path(self.settings.home))
            spec = overlay.apply_to_spec(spec)
            path = self.worktrees.worktree_path(ctx.project_slug, ctx.card_id)
            paths.assert_outside(path, all_checkouts)
            info = await asyncio.to_thread(
                self.worktrees.create_worktree, ctx.agent_path, path, branch, start_ref
            )
            if start_ref != ctx.default_branch:
                # A swarm part's rerun brings in what the other parts merged meanwhile; a
                # conflict is left for the builder to resolve, and its gates check the result.
                brought, conflicted = await asyncio.to_thread(
                    self.worktrees.bring_base, info.path, start_ref
                )
                if brought == "conflict" and conflicted:
                    clone.warnings.append(
                        f"merging {start_ref} into {branch} conflicts in: {', '.join(conflicted)}"
                    )
                    await asyncio.to_thread(
                        self._add_feedback, ctx.run_id, swarm.conflict_note(start_ref, conflicted)
                    )
            else:
                # A rerun's branch may predate the project file it now needs (a no-op otherwise).
                brought = await asyncio.to_thread(
                    self.worktrees.bring_project_files, info.path, ctx.default_branch
                )
                if brought == "conflict":
                    clone.warnings.append(
                        f"{ctx.default_branch} changed the project file, but merging it into "
                        f"{branch} conflicted; the card runs with the project file it had"
                    )
            if brought == "merged":
                info = replace(info, head=Git(info.path).head())
            if brought == "conflict" and start_ref != ctx.default_branch:
                # The merge completes with the builder's first commit; measured from the
                # epic's head, the card's change stays its own.
                base = await asyncio.to_thread(Git(ctx.agent_path).rev_parse, start_ref)
            else:
                base = await asyncio.to_thread(
                    self.worktrees.merge_base, ctx.agent_path, start_ref, branch
                )
        # The project's databases: a branch per card next to the git branch.
        databases: list[dict[str, Any]] = []
        try:
            for project_db in _databases(spec, self._environ(ctx.project_slug)):
                start = await asyncio.to_thread(project_db.create, ctx.card_id)
                databases.append(
                    {
                        "name": project_db.spec.name,
                        "database": project_db.database,
                        "branch": branch_name(ctx.card_id),
                        "start": start,
                    }
                )
        except WorkspaceError as exc:
            return StepResult(
                StepStatus.FAILED,
                {"databases": databases},
                RunStatus.FAILED,
                f"database workspace: {exc}",
                str(exc),
            )
        # A setup card exists because there is no project file yet, so the project's own level
        # (the default, low) says nothing; it runs at medium, under the installation's ceiling.
        project_level = Autonomy.MEDIUM if ctx.is_setup else spec.autonomy
        autonomy = lowest_autonomy(self.settings.max_autonomy, project_level, ctx.card_autonomy)
        with self.db.session() as session:
            run = _get_run(session, ctx.run_id)
            run.worktree_path = str(info.path)
            run.branch = branch
            run.base_commit = base
            run.start_commit = info.head
            run.spec = spec.model_dump(mode="json")
            run.autonomy = autonomy
            run.card.branch = branch
        return StepResult(
            detail={
                "worktree": str(info.path),
                "branch": branch,
                "worktree_created": info.created,
                "clone_created": clone.created,
                "base_commit": base,
                "start_commit": info.head,
                "autonomy": str(autonomy),
                "warnings": clone.warnings,
                "databases": databases,
                "overlay": {
                    "loaded": overlay.loaded,
                    "disabled": [f"{i.item}: {i.reason}" for i in overlay.disabled],
                },
            }
        )

    def _model(self, role: Role, ctx: _Ctx) -> str:
        return resolve_model(
            role,
            settings_models=load_overlay(overlay_path(self.settings.home)).models_under(
                self.settings.models
            ),
            project_models=ctx.spec.models if ctx.spec is not None else None,
        )

    def _add_cost(self, run_id: int, usd: float) -> float:
        with self.db.session() as session:
            run = _get_run(session, run_id)
            run.cost_usd = run.cost_usd + usd
            return run.cost_usd

    def _spent(self, run_id: int) -> float:
        with self.db.session() as session:
            return _get_run(session, run_id).cost_usd

    def _remaining_budget(self, ctx: _Ctx) -> float | None:
        if ctx.budget_usd is None:
            return None
        return max(ctx.budget_usd - self._spent(ctx.run_id), 0.0)

    def _journal_events(self, ctx: _Ctx, role: str) -> Callable[[RuntimeEvent], None]:
        def record(event: RuntimeEvent) -> None:
            ctx.journal.append(f"{role}.{event.type}", event_to_dict(event))
            self._observe(ctx.run_id, role, event)

        return record

    async def _introductions(self, ctx: _Ctx) -> None:
        """The first time a lead or helper works, it chooses its name, bio and personality
        (prat.identity), in the background: a run never waits for it, and a member still
        unnamed works under its default name. Scripted runtimes keep the default names."""
        if getattr(self.runtime, "name", "") == "fake" or self._introducing is not None:
            return
        from prat import crew, identity

        try:
            lead = crew.lead_for(ctx.project_spec.crew.model_dump(), ctx.worktree_path)
            keys = [lead.key, *(ctx.project_spec.crew.helpers or crew.HELPER_PROMPTS)]
            missing = [k for k in keys if identity.load(self.settings.home, k) is None]
        except Exception:
            log.exception("introductions failed for run %s", ctx.run_id)
            return
        if not missing:
            return
        model = resolve_model(Role.CREW, settings_models=self.settings.models)

        async def introduce() -> None:
            try:
                for key in missing:  # one at a time: each sees the names already taken
                    await identity.ensure(self.settings.home, self.runtime, key, model=model)
            except Exception:
                log.exception("introductions failed for run %s", ctx.run_id)
            finally:
                self._introducing = None

        self._introducing = asyncio.create_task(introduce(), name="prat-introductions")

    def _crew(self, ctx: _Ctx, role: str) -> crewing.CrewSession | None:
        """The lead, helpers, skills, memory and PRAT tools for a builder or setup session.
        A problem here leaves the agent without its crew, never without a run."""
        try:
            databases: dict[str, str] = {}
            with contextlib.suppress(WorkspaceError, SQLAlchemyError):
                for project_db in _databases(ctx.project_spec, self._environ(ctx.project_slug)):
                    databases[project_db.spec.name] = project_db.branch_url(ctx.card_id)
            from prat import jobs

            async def start_job(name: str) -> str:
                return await asyncio.to_thread(
                    jobs.start_for_agent,
                    self.settings,
                    self.db,
                    ctx.project_slug,
                    name,
                    ctx.card_id,
                )

            async def job_status(run: int | None) -> str:
                return await asyncio.to_thread(jobs.describe, self.db, ctx.card_id, run)

            return crewing.prepare(
                home=self.settings.home,
                project=ctx.project_slug,
                spec=ctx.project_spec,
                worktree=ctx.worktree_path,
                card_id=ctx.card_id,
                run_id=ctx.run_id,
                title=ctx.title,
                card_text=f"{ctx.goal}\n{ctx.acceptance_criteria}\n{ctx.feedback or ''}",
                role=role,
                record=ctx.journal.append,
                live=functools.partial(self._live, ctx.run_id),
                databases=databases,
                start_job=start_job,
                job_status=job_status,
                continuity=self.settings.continuity,
                continuity_with=self.settings.continuity_with,
            )
        except Exception:
            log.exception("could not assemble the crew for run %s", ctx.run_id)
            return None

    def _live(self, run_id: int, key: str, value: Any) -> None:
        """Set one live value for mission control (a tool's progress, the lead's name)."""
        try:
            telemetry = self._telemetry_of(run_id)
            telemetry.live[key] = value
            if telemetry.set_dirty():
                self._update_run(run_id, usage=telemetry.usage(), live=dict(telemetry.live))
        except Exception:
            log.exception("could not record a live value for run %s", run_id)

    def _telemetry_of(self, run_id: int) -> RunTelemetry:
        telemetry = self._telemetry.get(run_id)
        if telemetry is None:
            with self.db.session() as session:
                run = _get_run(session, run_id)
                telemetry = RunTelemetry.load(run.usage, run.live)
            self._telemetry[run_id] = telemetry
        return telemetry

    def _observe(self, run_id: int, role: str, event: RuntimeEvent) -> None:
        """Usage and live state for mission control (prat.engine.telemetry); rate limit
        pressure for the governor. Never fails a run."""
        try:
            if isinstance(event, PressureEvent):
                governor.record_pressure(event)
            telemetry = self._telemetry_of(run_id)
            if telemetry.observe(role, event):
                self._update_run(run_id, usage=telemetry.usage(), live=dict(telemetry.live))
        except Exception:  # telemetry is never worth a failed run
            log.exception("could not record telemetry for run %s", run_id)

    async def _knowledge(self, ctx: _Ctx, role: Role, extra: str = "") -> Selection:
        """What PRAT has learned about the project, selected for this card and role. The
        entry ids go in the journal and the step detail, so the Remember phase can credit
        the entries on a card that went well."""
        text = "\n".join([ctx.title, ctx.goal, ctx.acceptance_criteria, ctx.feedback or "", extra])
        selection = await asyncio.to_thread(
            for_card,
            self.settings.home,
            ctx.project_slug,
            ctx.worktree_path,
            text,
            str(role),
        )
        if selection.ids:
            ctx.journal.append(
                "knowledge",
                {"role": str(role), "ids": selection.ids, "considered": selection.considered},
            )
        return selection

    def _bench_memory(self, ctx: _Ctx, agent: str) -> str:
        """A bench agent's (planner, reviewer) memory, for its prompt, opened by its own
        story when its group gets continuity (prat.continuity)."""
        from prat import continuity

        try:
            recalled = memory.briefing(
                memory.AgentMemory(self.settings.home, agent),
                project=ctx.project_slug,
                card_id=ctx.card_id,
                query=f"{ctx.title}\n{ctx.goal}",
                budget=1500,
                episodes=3,
            )
            if continuity.gets_it(self.settings.continuity, agent, self.settings.continuity_with):
                continuity.started(self.settings.home)
                own = continuity.opening(self.settings.home, agent)
                return "\n\n".join(t for t in (own, recalled) if t)
            return recalled
        except Exception:
            log.exception("could not read the memory of %s", agent)
            return ""

    def _bench_episode(
        self, ctx: _Ctx, agent: str, what: str, feeling: memory.Feeling, importance: float = 0.4
    ) -> None:
        try:
            memory.AgentMemory(self.settings.home, agent).save(
                memory.new_episode(
                    agent,
                    project=ctx.project_slug,
                    card_id=ctx.card_id,
                    run_id=ctx.run_id,
                    role=agent,
                    title=ctx.title,
                    what=what[:1500],
                    feeling=feeling,
                    importance=importance,
                )
            )
        except Exception:
            log.exception("could not record the memory of %s", agent)

    async def _split_epic(self, ctx: _Ctx, knowledge: str, remaining: float | None) -> StepResult:
        """An epic's run: the planner proposes its parts, which become draft child cards; the
        run then ends and the epic waits for the person to approve the split (prat.epics)."""
        from prat import epics
        from prat.runtime.session import collect

        recalled = await asyncio.to_thread(self._bench_memory, ctx, "planner")
        task = AgentTask(
            prompt="Split this epic into parts. Answer with the JSON only.",
            system_prompt="\n\n".join(
                t
                for t in (
                    epics.EPIC_PROMPT.format(lo=epics.MIN_PARTS, hi=epics.MAX_PARTS),
                    _card_text(ctx),
                    project_facts(ctx.project_spec, Autonomy.READ_ONLY),
                    knowledge,
                    recalled,
                )
                if t
            ),
            workdir=ctx.worktree_path,
            allowed_tools=READ_TOOLS,
            policy=self._guarded(ctx, Autonomy.READ_ONLY, "planner"),
            budget=Budget(max_usd=remaining),
            model=self._model(Role.PLANNER, ctx),
            metadata={"role": "planner", "card_id": str(ctx.card_id)},
        )
        out = await collect(self.runtime, task, on_event=self._journal_events(ctx, "planner"))
        if out.cost_usd:
            self._add_cost(ctx.run_id, out.cost_usd)
        try:
            parts = epics.parse_parts(out.final_text)
        except epics.EpicError as exc:
            return StepResult(
                StepStatus.FAILED, {"error": str(exc)}, RunStatus.FAILED, str(exc), str(exc)
            )
        ids = await asyncio.to_thread(epics.create_parts, self.db, ctx.card_id, parts)
        await asyncio.to_thread(
            self._bench_episode,
            ctx,
            "planner",
            "Split the epic into: " + "; ".join(p.title for p in parts),
            memory.Feeling("thoughtful", 0.3, 0.4),
            0.6,
        )
        reason = f"split into {len(ids)} parts: " + ", ".join(f"#{i}" for i in ids)
        return StepResult(
            StepStatus.SUCCEEDED,
            {"parts": ids, "epic": True},
            RunStatus.SUCCEEDED,
            reason,
        )

    async def _approve_epic(self, card_id: int) -> MergeResult:
        from prat import epics

        with self.db.session() as session:
            card = store.get_card(session, card_id)
            branch = card.branch or ""
        if self.settings.swarm_epics:
            ready = await asyncio.to_thread(swarm.start, self.db, card_id)
        else:
            ready = await asyncio.to_thread(epics.approve_split, self.db, card_id)
        for part in ready:
            with contextlib.suppress(EngineError):
                self.submit(part)
        return MergeResult(card_id, branch, "")

    # Swarms (prat.swarm) ----------------------------------------------------------

    def _rival_events(
        self, ctx: _Ctx, active: _Active, worktree: Path
    ) -> Callable[[RuntimeEvent], None]:
        record = self._journal_events(ctx, "rival")
        watch = self._watch(ctx, active, worktree)

        def observe(event: RuntimeEvent) -> None:
            record(event)
            watch(event)

        return observe

    def _watch(self, ctx: _Ctx, active: _Active, worktree: Path) -> Callable[[Any], None]:
        """Watch a builder's tool calls: when it runs the tests after editing, start the
        reviewer on a snapshot of its worktree (prat.engine.speculative)."""
        if (
            self.reviewer is None
            or not self.settings.review_during_gates
            or not self.settings.speculative_review
            or ctx.is_setup
            or ctx.project_spec.workspace.database
            or not ctx.project_spec.verify.gates
        ):
            return lambda event: None
        markers = speculative.test_markers(ctx.project_spec)

        def observe(event: Any) -> None:
            if not isinstance(event, ToolCallEvent) or event.tool.lower() != "bash":
                return
            command = str((event.input or {}).get("command", ""))
            if speculative.runs_tests(command, markers):
                task = asyncio.create_task(self._speculate(ctx, active, worktree))
                self._setups.add(task)
                task.add_done_callback(self._setups.discard)

        return observe

    async def _speculate(self, ctx: _Ctx, active: _Active, worktree: Path) -> None:
        try:
            shot = await asyncio.to_thread(speculative.snapshot, worktree)
            if shot is None or ctx.base is None:
                return
            tree, commit = shot
            slot = str(worktree)
            previous = active.speculative.get(slot)
            if previous is not None:
                if previous[0] == tree:
                    return
                previous[1].cancel()
                active.speculative.pop(slot, None)
            stats = await asyncio.to_thread(self.worktrees.diff_stats, worktree, ctx.base, commit)
            if stats.empty:
                return
            diff = await asyncio.to_thread(self.worktrees.diff_text, worktree, ctx.base, commit)
            ctx.journal.append("critique.speculative", {"tree": tree, "worktree": str(worktree)})
            self._start_early_review(
                ctx, active, stats, diff, ctx.project_spec, tree=tree, worktree=worktree, slot=slot
            )
        except Exception:
            log.exception("a speculative review of run %s could not start", ctx.run_id)

    async def _start_race(
        self,
        ctx: _Ctx,
        task: AgentTask,
        sandbox: Sandbox | None,
        env: Mapping[str, str],
        active: _Active,
    ) -> Race | None:
        """A rival builder for this card (prat.engine.race), or ``None`` when the card does not
        race: a resumed session, a setup or infra card, or one with its own database branch."""
        if (
            self.settings.race_builders < 2
            or ctx.session_id
            or ctx.card_kind is not CardKind.CHANGE
            or ctx.is_setup
            or env
        ):
            return None
        lead_path = ctx.worktree_path
        path = lead_path.parent / f"{lead_path.name}-race"
        branch = f"{ctx.branch or card_branch(ctx.card_id, ctx.title)}-race"
        try:
            start = await asyncio.to_thread(Git(lead_path).head)
            await asyncio.to_thread(self.worktrees.remove_worktree, ctx.agent_path, path)
            await asyncio.to_thread(
                Git(ctx.agent_path).run, "branch", "-D", "--quiet", branch, check=False
            )
            await asyncio.to_thread(
                self.worktrees.create_worktree, ctx.agent_path, path, branch, start
            )
        except (GitError, CloneError, OSError) as exc:
            ctx.journal.append("race.skipped", {"reason": str(exc)})
            return None
        rival_ctx = dataclasses.replace(ctx, worktree=path)
        rival_sandbox = None
        if sandbox is not None:
            try:
                rival_sandbox = await asyncio.to_thread(self._sandbox, rival_ctx, env)
            except SandboxError as exc:
                ctx.journal.append("race.skipped", {"reason": str(exc)})
                await asyncio.to_thread(self.worktrees.remove_worktree, ctx.agent_path, path)
                return None
        rival_task = dataclasses.replace(
            task,
            workdir=path,
            policy=self._guarded(rival_ctx, None, "rival"),
            shell=(
                functools.partial(rival_sandbox.shell, cwd=path, env=env)
                if rival_sandbox is not None
                else None
            ),
            # The crew's own tools write the card's memory and journal: the lead's alone.
            helpers={},
            prat_tools=(),
            metadata={**task.metadata, "role": "rival"},
        )
        ctx.journal.append("race.started", {"rival": str(path), "start": start})
        return Race(
            runtime=self.runtime,
            task=rival_task,
            worktrees=self.worktrees,
            agent_path=ctx.agent_path,
            lead_path=lead_path,
            path=path,
            branch=branch,
            start=start,
            on_event=self._rival_events(ctx, active, path),
            sandbox=rival_sandbox,
        )

    def _is_swarm_part(self, card_id: int) -> bool:
        with self.db.session() as session:
            return bool(store.get_card(session, card_id).base_branch)

    def _after_run(self, card_id: int, work: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Start ``work`` once the card's current run task has finished (it may queue the card
        again, which its own task cannot)."""
        current = self._tasks.get(card_id)

        async def later() -> None:
            if current is not None and current is not asyncio.current_task():
                await asyncio.wait({current})
            try:
                await work()
            except Exception:
                log.exception("swarm work for card %s failed", card_id)

        task = asyncio.create_task(later(), name=f"prat-swarm-{card_id}")
        self._setups.add(task)
        task.add_done_callback(self._setups.discard)

    def _add_feedback(self, run_id: int, text: str) -> None:
        with self.db.session() as session:
            run = _get_run(session, run_id)
            run.feedback = f"{run.feedback}\n\n{text}" if run.feedback else text

    async def _swarm_part_finished(self, card_id: int) -> None:
        """A swarm part reached review (its gates and reviewer passed): merge it into its epic's
        branch, start the parts that were waiting on it, and finish the epic after the last."""
        with self.db.session() as session:
            card = store.get_card(session, card_id)
            epic = session.get(Card, card.parent_id) if card.parent_id is not None else None
            if (
                card.status != CardStatus.REVIEW
                or not card.base_branch
                or not card.branch
                or epic is None
                or epic.status != CardStatus.BUILDING
                or epic.branch != card.base_branch
            ):
                return
            epic_id, epic_branch, branch = epic.id, card.base_branch, card.branch
            project_id, slug = card.project_id, card.project.slug
            agent, title, runs = Path(card.project.agent_path), card.title, len(card.runs)
        if await asyncio.to_thread(
            swarm.conflict_markers, self.worktrees, agent, epic_branch, branch
        ):
            await self._swarm_send_back(
                card_id, epic_branch, [], "Your change still has conflict markers in it."
            )
            return
        async with self._project_lock(project_id):
            try:
                commit = await asyncio.to_thread(
                    swarm.integrate,
                    self.worktrees,
                    agent,
                    epic_branch,
                    branch,
                    f"Merge part #{card_id} into epic #{epic_id}: {title}",
                )
            except MergeConflict as exc:
                conflicted: list[str] | None = exc.files
            else:
                conflicted = None
        if conflicted is not None:
            await self._swarm_send_back(card_id, epic_branch, conflicted, "")
            return
        with self.db.session() as session:
            card = store.get_card(session, card_id)
            store.set_card_status(
                session, card, CardStatus.DONE, f"merged into epic #{epic_id} as {commit[:10]}"
            )
            store.record_event(
                session,
                "card.integrated",
                project_id=project_id,
                card_id=card_id,
                data={
                    "epic": epic_id,
                    "branch": branch,
                    "epic_branch": epic_branch,
                    "commit": commit,
                },
            )
        versioning.commit(
            self.db,
            f"card {card_id} merged into epic {epic_id} ({epic_branch}) as git {commit}",
            actor="engine (swarm)",
        )
        self._settle(slug, card_id, "landed", first_review=runs == 1)
        remaining = await asyncio.to_thread(swarm.parts, self.db, epic_id)
        for part in remaining:
            if part.status in (CardStatus.READY, CardStatus.REWORK) and not self.is_scheduled(
                part.id
            ):
                with contextlib.suppress(EngineError):
                    self.submit(part.id)
        if remaining and all(p.status == CardStatus.DONE for p in remaining):
            await self._finish_epic(epic_id)

    async def _swarm_send_back(
        self, card_id: int, epic_branch: str, files: list[str], why: str
    ) -> None:
        """The part does not merge cleanly into its epic: back to its builder, whose next run
        starts with the epic's branch merged in. After too many rounds the person decides."""
        rounds = await asyncio.to_thread(swarm.conflict_rounds, self.db, card_id)
        note = why or (
            f"Your change conflicts with work merged into {epic_branch} in: {', '.join(files)}."
        )
        with self.db.session() as session:
            card = store.get_card(session, card_id)
            if rounds >= swarm.MAX_CONFLICT_ROUNDS:
                store.add_comment(
                    session,
                    card,
                    f"{note} It has gone back {rounds} times; it waits in review for you.",
                    author=CommentAuthor.SYSTEM,
                    kind="merge_conflict",
                    data={"files": files},
                )
                return
            store.add_comment(
                session,
                card,
                note,
                author=CommentAuthor.SYSTEM,
                kind="swarm_conflict",
                data={"files": files, "epic_branch": epic_branch},
            )
            store.set_card_status(
                session, card, CardStatus.REWORK, "does not merge into its epic: sent back"
            )
        with contextlib.suppress(EngineError):
            self.submit(card_id)

    async def _finish_epic(self, epic_id: int) -> None:
        """Every part is in: run the project's gates on the epic's branch, the whole feature,
        then bring the epic to review (or to failed, with the gate that failed)."""
        with self.db.session() as session:
            epic = store.get_card(session, epic_id)
            if epic.status != CardStatus.BUILDING or not epic.branch:
                return
            project = epic.project
            agent, slug, branch = Path(project.agent_path), project.slug, epic.branch
            default, fallback = project.default_branch, _spec_of(project)
        spec = _spec_from_branch(agent, branch) or fallback
        spec = load_overlay(overlay_path(self.settings.home)).apply_to_spec(spec)
        path = self.worktrees.worktrees_root / "_swarm" / f"epic-{epic_id}-gates"
        head = await asyncio.to_thread(Git(agent).rev_parse, f"refs/heads/{branch}")
        environ = self._environ(slug)
        dbs: list[DoltBranchProvider] = []
        gates: list[GateResult] = []
        problem: str | None = None
        try:
            await asyncio.to_thread(self.worktrees.remove_worktree, agent, path)
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                Git(agent).run, "worktree", "add", "--quiet", "--detach", str(path), head
            )
            dbs = _databases(spec, environ)
            for project_db in dbs:
                await asyncio.to_thread(project_db.create, epic_id)
            env = _database_env(spec, epic_id, environ)
            sandbox = sandbox_for(
                self.settings,
                name=f"{slug}-epic-{epic_id}",
                workdir=path,
                network=False,
                env=env,
                image=spec.sandbox.image,
            )
            log_dir = self.settings.home / "runs" / f"epic-{epic_id}"
            log_dir.mkdir(parents=True, exist_ok=True)
            gates = await asyncio.to_thread(
                functools.partial(
                    run_gates,
                    spec,
                    path,
                    log_dir,
                    timeout=min(spec.verify.timeout_seconds, self.settings.gate_timeout_seconds),
                    env=env or None,
                    sandbox=sandbox,
                )
            )
            if sandbox is not None:
                await asyncio.to_thread(sandbox.cleanup)
        except (SandboxError, WorkspaceError, GitError, OSError) as exc:
            problem = str(exc)
        finally:
            for project_db in dbs:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(project_db.discard, epic_id)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.worktrees.remove_worktree, agent, path)
        failed = [g for g in gates if not g.passed]
        stats = await asyncio.to_thread(
            self.worktrees.diff_stats,
            agent,
            await asyncio.to_thread(self.worktrees.merge_base, agent, default, branch),
            branch,
        )
        parts = await asyncio.to_thread(swarm.parts, self.db, epic_id)
        lines = [
            f"A swarm built this epic in {len(parts)} parts, each merged into {branch}:",
            "",
            *(f"- #{p.id} {p.title}" for p in parts),
            "",
            f"The whole feature: {len(stats.files)} files, +{stats.insertions} -{stats.deletions}.",
        ]
        if problem:
            lines.append(f"The gates could not run on the epic's branch: {problem}")
        elif failed:
            lines.append(
                f"The gate `{failed[0].name}` failed on the epic's branch:\n{failed[0].output_tail}"
            )
        else:
            names = ", ".join(g.name for g in gates) or "none configured"
            lines.append(f"The project's gates passed on the epic's branch ({names}).")
        with self.db.session() as session:
            epic = store.get_card(session, epic_id)
            store.add_comment(
                session,
                epic,
                "\n".join(lines),
                author=CommentAuthor.SYSTEM,
                kind="epic_summary",
                data={"head": head, "gates": [g.name for g in gates if g.passed]},
            )
            epic.summary = render_summary(stats)
            store.record_event(
                session,
                "epic.verified",
                project_id=epic.project_id,
                card_id=epic_id,
                data={"head": head, "passed": not (problem or failed)},
            )
            if problem or failed:
                store.set_card_status(
                    session,
                    epic,
                    CardStatus.FAILED,
                    problem or f"gate {failed[0].name} failed on the epic's branch",
                )
            else:
                store.set_card_status(
                    session, epic, CardStatus.REVIEW, "every part merged; the whole feature passed"
                )
        versioning.commit(self.db, f"epic {epic_id} built by a swarm", actor="engine (swarm)")

    async def _land_epic(self, epic_id: int) -> MergeResult:
        """Approve a swarm epic: merge its branch into the default branch, with each part's
        database branch, all or nothing."""
        with self.db.session() as session:
            epic = store.get_card(session, epic_id)
            project = epic.project
            agent, checkout = Path(project.agent_path), Path(project.checkout_path)
            branch, default, title = epic.branch or "", project.default_branch, epic.title
            verified = next(
                (
                    e.data.get("head")
                    for e in reversed(store.events_for_card(session, epic))
                    if e.kind == "epic.verified" and e.data.get("passed")
                ),
                None,
            )
            spec = _spec_of(project)
        part_ids = [p.id for p in await asyncio.to_thread(swarm.parts, self.db, epic_id)]
        async with self._project_lock(project.id):
            head = await asyncio.to_thread(Git(agent).rev_parse, f"refs/heads/{branch}")
            if verified != head:
                raise ApproveError(
                    f"epic {epic_id}: {branch} is at {head[:10]} but its gates passed at "
                    f"{(verified or 'no commit')[:10]}"
                )
            try:
                project_dbs = _databases(spec, self._environ(project.slug))
            except WorkspaceError as exc:
                raise ApproveError(f"epic {epic_id}: {exc}") from exc
            merged_data: list[tuple[DoltBranchProvider, str, str]] = []
            try:
                for part_id in part_ids:
                    for project_db in project_dbs:
                        if await asyncio.to_thread(project_db.exists, part_id):
                            before, after = await asyncio.to_thread(
                                project_db.merge, part_id, f"Merge part {part_id} of epic {epic_id}"
                            )
                            merged_data.append((project_db, before, after))
            except (DatabaseMergeConflict, WorkspaceError, SQLAlchemyError) as exc:
                await _undo_data(merged_data)
                raise ApproveError(f"epic {epic_id}: database merge failed: {exc}") from exc
            try:
                commit = await asyncio.to_thread(
                    self.worktrees.merge, agent, branch, default, f"Merge epic {epic_id}: {title}"
                )
            except MergeConflict as exc:
                await _undo_data(merged_data)
                raise ApproveError(str(exc)) from exc
            except BaseException:
                await _undo_data(merged_data)
                raise
            followed = await asyncio.to_thread(
                _follow_checkout, checkout, agent, default, onboard.projects_dir(self.settings)
            )
        with self.db.session() as session:
            epic = store.get_card(session, epic_id)
            store.set_card_status(session, epic, CardStatus.DONE, f"merged as {commit[:10]}")
            store.record_event(
                session,
                "card.merged",
                project_id=epic.project_id,
                card_id=epic_id,
                data={
                    "commit": commit,
                    "branch": branch,
                    "branch_head": head,
                    "checkout": followed,
                },
            )
        if versioning.commit(
            self.db, f"epic {epic_id} approved: merged {branch} as git {commit}", actor="person"
        ):
            versioning.tag(self.db, f"card-{epic_id}-approved", message=f"git {commit}")
        if followed == "updated":
            self._start_checkout_setup(project.slug, epic_id)
        return MergeResult(epic_id, branch, commit)

    def _epic_progress(self, card_id: int) -> None:
        from prat import epics

        try:
            epics.on_part_landed(self.db, card_id)
        except Exception:
            log.exception("could not update the epic of card %s", card_id)

    async def _step_plan(self, ctx: _Ctx, active: _Active) -> StepResult:
        remaining = self._remaining_budget(ctx)
        knowledge = await self._knowledge(ctx, Role.PLANNER)
        if ctx.card_kind is CardKind.EPIC:
            return await self._split_epic(ctx, knowledge.text, remaining)
        if not self.settings.plan_pass:
            # The builder plans in its own session: the codebase is read once, not twice.
            return StepResult(StepStatus.SKIPPED, {"reason": "the builder plans as it starts"})
        recalled = await asyncio.to_thread(self._bench_memory, ctx, "planner")
        raw = await self.planner.plan(
            PlanRequest(
                card_id=ctx.card_id,
                title=ctx.title,
                goal=ctx.goal,
                acceptance_criteria=ctx.acceptance_criteria,
                complexity=ctx.complexity,
                workdir=str(ctx.worktree_path),
                spec=ctx.spec,
                policy=self._guarded(ctx, Autonomy.READ_ONLY, "planner"),
                model=self._model(Role.PLANNER, ctx),
                budget_usd=remaining,
                on_event=self._journal_events(ctx, "planner"),
                knowledge="\n\n".join(t for t in (knowledge.text, recalled) if t),
            )
        )
        if raw is not None:
            plan_text = raw if isinstance(raw, str) else (raw.plan or raw.blocked_reason or "")
            await asyncio.to_thread(
                self._bench_episode,
                ctx,
                "planner",
                f"Planned it: {plan_text[:1200]}",
                memory.Feeling("focused", 0.2, 0.3),
            )
        if raw is None:
            return StepResult(StepStatus.SKIPPED, detail={"reason": "no plan produced"})
        result = PlanResult(plan=raw) if isinstance(raw, str) else raw
        total = self._add_cost(ctx.run_id, result.cost_usd) if result.cost_usd else None
        detail: dict[str, Any] = {"model": self._model(Role.PLANNER, ctx)}
        if knowledge.ids:
            detail["knowledge"] = knowledge.ids
        if result.cost_usd:
            detail["cost_usd"] = round(result.cost_usd, 6)
        if result.blocked_reason:
            # A blocked plan decides the run's outcome; no later step may replace it.
            reason = result.blocked_reason
            detail["blocked_reason"] = reason
            return StepResult(
                StepStatus.FAILED, detail, RunStatus.BLOCKED, reason, f"blocked: {reason}"
            )
        if total is not None and ctx.budget_usd is not None and total > ctx.budget_usd:
            message = f"budget exceeded while planning: spent ${total:.2f} of ${ctx.budget_usd:.2f}"
            return StepResult(
                StepStatus.FAILED, detail, RunStatus.FAILED, message, "budget exceeded"
            )
        if not result.plan:
            detail["reason"] = result.error or "no plan produced"
            return StepResult(StepStatus.SKIPPED, detail=detail)
        detail["plan"] = result.plan
        return StepResult(detail=detail)

    def _sandbox(
        self, ctx: _Ctx, env: Mapping[str, str], spec: ProjectSpec | None = None
    ) -> Sandbox | None:
        """The card's sandbox when the installation uses one. The setup card installs
        dependencies, so its containers have the network; no other card's do."""
        return sandbox_for(
            self.settings,
            name=f"{ctx.project_slug}-card-{ctx.card_id}",
            workdir=ctx.worktree_path,
            network=ctx.is_setup,
            env=env,
            image=(spec or ctx.project_spec).sandbox.image,
        )

    def _policy(self, ctx: _Ctx, autonomy: Autonomy | None = None) -> Policy:
        with self.db.session() as session:
            roots = [Path(p.checkout_path) for p in store.list_projects(session)]
            roots += [Path(p.agent_path) for p in store.list_projects(session)]
        roots.append(self.settings.home)
        level = autonomy or ctx.run_autonomy
        return Policy(
            level,
            ctx.worktree_path,
            protected=ctx.project_spec.protected_paths,
            secret_paths=ctx.project_spec.secret_paths,
            denied_roots=roots,
            project_commands=[
                command_argv(c) for c in ctx.project_spec.commands.defined().values()
            ],
            mcp_tools=self.mcp_tools if autonomy is None else (),
            denied_commands=load_overlay(overlay_path(self.settings.home)).policy_denied_commands(),
            # The setup card's builder is the one agent that writes the project file.
            project_files=autonomy is None and ctx.is_setup,
        )

    def _guarded(
        self, ctx: _Ctx, autonomy: Autonomy | None, agent: str
    ) -> Callable[[ToolCall], Decision]:
        """The policy for one agent, behind the loop detector, journaling every decision."""
        policy = self._policy(ctx, autonomy)
        loops = LoopDetector(self.settings.loop_threshold, self.settings.loop_window)
        journal = ctx.journal

        def guarded(call: ToolCall) -> Decision:
            denial = loops.check(call)
            decision = denial if denial is not None else policy.decide(call)
            journal.append(
                "policy",
                {
                    "agent": agent,
                    "call": call.describe(),
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                    "loop": denial is not None,
                },
            )
            return decision

        return guarded

    async def _step_execute(self, ctx: _Ctx, active: _Active) -> StepResult:
        guarded = self._guarded(ctx, None, "builder")
        journal = ctx.journal
        role = Role.SETUP if ctx.is_setup else Role.BUILDER
        model = self._model(role, ctx)

        tools = set(READ_TOOLS) | {"bash"}
        if ctx.run_autonomy.at_least(Autonomy.LOW):
            tools = set(ALL_TOOLS) if self.mcp_tools else set(ALL_TOOLS) - {"mcp"}
        remaining = self._remaining_budget(ctx)
        # The repository's AGENTS.md and CLAUDE.md, if any. Settings discovery is off in
        # agent sessions, so this is the only way they reach the builder.
        instructions = await asyncio.to_thread(load_repo_instructions, ctx.worktree_path)
        knowledge = await self._knowledge(ctx, Role.BUILDER, ctx.plan or "")
        env = _database_env(ctx.project_spec, ctx.card_id, self._environ(ctx.project_slug))
        if ctx.card_kind is CardKind.INFRA:
            variables = load_local(self.settings.home, ctx.project_slug).variables
            env = {**variables, **env}
            system_prompt = infra_system_prompt(
                name=ctx.project_spec.name,
                default_branch=ctx.default_branch,
                autonomy=ctx.run_autonomy,
                database=DATABASE_VARIABLE in variables,
                repo_instructions=repository_instructions(instructions),
            ) + _card_text(ctx)
            task_prompt = builder_task_prompt(feedback=ctx.feedback, plan=ctx.plan).replace(
                "Implement the card described in your instructions.", INFRA_TASK, 1
            )
        elif ctx.is_setup:
            variables = load_local(self.settings.home, ctx.project_slug).variables
            env = {**variables, **env}
            system_prompt = setup_system_prompt(
                name=ctx.project_spec.name,
                default_branch=ctx.default_branch,
                autonomy=ctx.run_autonomy,
                database=DATABASE_VARIABLE in variables,
                mysql=MYSQL_DATABASE_VARIABLE in variables,
                repo_instructions=repository_instructions(instructions),
            ) + _card_text(ctx)
            task_prompt = builder_task_prompt(feedback=ctx.feedback, plan=ctx.plan).replace(
                "Implement the card described in your instructions.", SETUP_TASK, 1
            )
        else:
            files = ""
            if not ctx.session_id:
                # A resumed session has read them already.
                files = await asyncio.to_thread(
                    context_pack.pack,
                    ctx.worktree_path,
                    f"{ctx.title}\n{ctx.goal}\n{ctx.acceptance_criteria}",
                    self.settings.context_pack_chars,
                )
            system_prompt = builder_system_prompt(
                title=ctx.title,
                goal=ctx.goal,
                acceptance_criteria=ctx.acceptance_criteria,
                spec=ctx.project_spec,
                autonomy=ctx.run_autonomy,
                repo_instructions=instructions,
                knowledge="\n\n".join(t for t in (knowledge.text, files) if t),
            )
            task_prompt = builder_task_prompt(feedback=ctx.feedback, plan=ctx.plan)
        try:
            sandbox = await asyncio.to_thread(self._sandbox, ctx, env)
        except SandboxError as exc:
            return StepResult(
                StepStatus.FAILED,
                {"sandbox": str(exc)},
                RunStatus.FAILED,
                f"sandbox: {exc}",
                "sandbox unavailable",
            )
        if sandbox is not None:
            system_prompt += SANDBOX_NOTE.format(network="has" if sandbox.network else "has no")
        await self._introductions(ctx)
        crew_session = await asyncio.to_thread(self._crew, ctx, str(role))
        if crew_session is not None:
            tools |= crew_session.tools
            system_prompt = crew_session.prompt + "\n\n" + system_prompt
        task = AgentTask(
            prompt=task_prompt,
            system_prompt=system_prompt,
            workdir=ctx.worktree_path,
            allowed_tools=frozenset(tools),
            policy=guarded,
            budget=Budget(max_usd=remaining),
            model=model,
            metadata={
                "card_id": str(ctx.card_id),
                "run_id": str(ctx.run_id),
                "project": ctx.project_slug,
                "role": str(role),
            },
            # The project's own database settings, pointed at the card's branch.
            env=env,
            shell=(
                functools.partial(sandbox.shell, cwd=ctx.worktree_path, env=env)
                if sandbox is not None
                else None
            ),
            helpers=crew_session.helpers if crew_session else {},
            skill_dirs=crew_session.skill_dirs if crew_session else (),
            prat_tools=crew_session.prat_tools if crew_session else (),
            thinking=bool(
                crew_session and crew_session.thinking and self.settings.builder_thinking
            ),
        )
        journal.append(
            "execute.model",
            {
                "model": model,
                "lead": crew_session.member.name if crew_session else None,
                "skills": crew_session.skills if crew_session else [],
                "helpers": sorted(crew_session.helpers) if crew_session else [],
                "tools": sorted(tools),
                "repo_instructions": [item.name for item in instructions],
                "sandbox": sandbox.image if sandbox is not None else None,
            },
        )
        if ctx.session_id:
            active.session_id = ctx.session_id
            stream = self.runtime.resume(ctx.session_id, task)
        else:
            stream = self.runtime.run(task)

        with self.db.session() as session:
            run = _get_run(session, ctx.run_id)
            cost = run.cost_usd
        spent_before = cost
        outcome: Outcome | None = None
        message = ""
        blocked_reason: str | None = None

        async def lead() -> None:
            nonlocal cost, outcome, message, blocked_reason
            try:
                async for event in stream:
                    journal.append(f"runtime.{event.type}", event_to_dict(event))
                    self._observe(ctx.run_id, str(role), event)
                    watch(event)
                    if crew_session is not None:
                        crew_session.observe(event)
                    if isinstance(event, SessionStarted):
                        active.session_id = event.session_id
                        self._update_run(ctx.run_id, session_id=event.session_id)
                    elif isinstance(event, CostEvent):
                        cost += event.usd
                        self._update_run(ctx.run_id, cost_usd=cost)
                        if ctx.budget_usd is not None and cost > ctx.budget_usd:
                            outcome = Outcome.BUDGET_EXCEEDED
                            message = f"budget exceeded: spent ${cost:.2f} of ${ctx.budget_usd:.2f}"
                            if active.session_id:
                                await self.runtime.cancel(active.session_id)
                            break
                    elif isinstance(event, BlockedEvent):
                        blocked_reason = event.reason
                    elif isinstance(event, DoneEvent):
                        outcome = event.outcome
                        message = event.message
                        break
                    if active.cancel_requested:
                        outcome = Outcome.CANCELLED
                        break
            finally:
                closer = getattr(stream, "aclose", None)
                if closer is not None:
                    await closer()

        watch = self._watch(ctx, active, ctx.worktree_path)
        race = await self._start_race(ctx, task, sandbox, env, active)
        race_detail: dict[str, Any] = {}
        try:
            if race is None:
                await lead()
            else:
                won = await race.run(lead, lambda: outcome, lambda: active.session_id)
                race_detail = race.detail
                journal.append("race.finished", race_detail)
                if won is not None:
                    # The other builder finished first with a change: the card takes its work.
                    outcome, message = won.outcome, won.message
                    blocked_reason = won.blocked_reason
                    active.session_id = won.session_id
                    self._update_run(ctx.run_id, session_id=won.session_id)
                    await asyncio.to_thread(race.adopt, ctx.worktree_path)
                cost += race.cost_usd
                self._update_run(ctx.run_id, cost_usd=cost)
        finally:
            if race is not None:
                # The rival's worktree goes in the background: the run moves on at once, and
                # a review still reading it finishes first.
                reading = [review for _, review in active.speculative.values()]
                if active.early_review is not None:
                    reading.append(active.early_review[1])

                async def remove_rival(race: Race = race) -> None:
                    if reading:
                        await asyncio.wait(set(reading), timeout=600)
                    await asyncio.to_thread(race.cleanup)

                cleanup = asyncio.create_task(remove_rival())
                self._setups.add(cleanup)
                cleanup.add_done_callback(self._setups.discard)
            if sandbox is not None:
                # Commands the agent's runtime gave up on leave their containers behind.
                await asyncio.to_thread(sandbox.cleanup)
        tampered = await asyncio.to_thread(sandbox.tampered) if sandbox is not None else None

        detail: dict[str, Any] = {
            "session_id": active.session_id,
            "model": model,
            # This step's own spend; the run row holds the running total.
            "cost_usd": round(cost - spent_before, 6),
            "outcome": str(outcome) if outcome else None,
            "message": message,
        }
        if race_detail:
            detail["race"] = race_detail
        if knowledge.ids:
            detail["knowledge"] = knowledge.ids
        if crew_session is not None:
            detail["lead"] = crew_session.member.name
            detail["skills_carried"] = crew_session.skills
            if crew_session.tool_ctx.risks:
                detail["risks"] = list(crew_session.tool_ctx.risks)
            await asyncio.to_thread(
                crew_session.finish, str(outcome) if outcome else "failed", message
            )
        telemetry = self._telemetry.get(ctx.run_id)
        if telemetry is not None:
            usage = telemetry.role_usage(str(role))
            if usage:
                detail["usage"] = usage
            used = sorted({s for s in telemetry.live.get("skills") or []})
            if used:
                detail["skills_used"] = used
            helpers = [h.get("name") for h in telemetry.live.get("helpers") or []]
            if helpers:
                detail["helpers_called"] = helpers
        if tampered is not None:
            detail["sandbox"] = tampered
            record_error(
                self.db,
                "sandbox",
                "git_tampered",
                f"a container changed what git on this machine reads: {tampered}",
                detail={"run_id": ctx.run_id, "card_id": ctx.card_id},
            )
            return StepResult(
                StepStatus.FAILED,
                detail,
                RunStatus.FAILED,
                f"sandbox: {tampered}",
                "the sandbox was tampered with",
            )
        if outcome is None:
            return StepResult(
                StepStatus.FAILED,
                detail,
                RunStatus.FAILED,
                "runtime ended without a result",
                "no result",
            )
        if outcome == Outcome.CANCELLED:
            return StepResult(
                StepStatus.FAILED, detail, RunStatus.CANCELLED, "cancelled by request", "cancelled"
            )
        if outcome == Outcome.BLOCKED or blocked_reason:
            reason = blocked_reason or message or "agent reported it is blocked"
            detail["blocked_reason"] = reason
            return StepResult(
                StepStatus.FAILED, detail, RunStatus.BLOCKED, reason, f"blocked: {reason}"
            )
        if outcome == Outcome.BUDGET_EXCEEDED:
            return StepResult(
                StepStatus.FAILED,
                detail,
                RunStatus.FAILED,
                message or "budget exceeded",
                "budget exceeded",
            )
        if outcome == Outcome.ERROR:
            record_error(
                self.db,
                "runtime",
                "agent_error",
                message or "the agent runtime reported an error",
                detail={"run_id": ctx.run_id, "card_id": ctx.card_id, "model": model},
            )
            return StepResult(
                StepStatus.FAILED, detail, RunStatus.FAILED, f"agent error: {message}", message
            )

        commit = await asyncio.to_thread(
            self.worktrees.safety_commit,
            ctx.worktree_path,
            f"{ctx.title}\n\nCard {ctx.card_id}, run {ctx.run_id}.",
        )
        stats = await asyncio.to_thread(self.worktrees.diff_stats, ctx.worktree_path, ctx.start)
        detail["safety_commit"] = commit
        detail["files_changed"] = len(stats.files)
        # What the agent wrote to its database branches becomes a commit there too.
        data_changed = False
        for project_db in _databases(ctx.project_spec, self._environ(ctx.project_slug)):
            head = await asyncio.to_thread(
                project_db.commit, ctx.card_id, f"card {ctx.card_id} run {ctx.run_id}: {ctx.title}"
            )
            tables = await asyncio.to_thread(project_db.changes, ctx.card_id)
            data_changed = data_changed or bool(tables)
            detail.setdefault("database_commits", []).append(
                {"name": project_db.spec.name, "head": head, "tables": len(tables)}
            )
        empty = empty_diff_finding(stats)
        if empty is not None:
            # A rework, or a card run again after a failed check, may have nothing new to
            # write: the card's earlier work is on the branch, and that is what verify and
            # review judge. A first run has nothing on the branch, so it still fails here.
            card_stats = await asyncio.to_thread(
                self.worktrees.diff_stats, ctx.worktree_path, ctx.base
            )
            if empty_diff_finding(card_stats) is None:
                detail["no_new_changes"] = True
                empty = None
        # A card that changes only data is still a change.
        if empty is not None and not data_changed:
            return StepResult(
                StepStatus.FAILED,
                detail,
                RunStatus.FAILED,
                "execute produced no changes",
                empty.message,
            )
        return StepResult(detail=detail)

    async def _step_verify(self, ctx: _Ctx, active: _Active) -> StepResult:
        worktree, spec, base = ctx.worktree_path, ctx.project_spec, ctx.base
        stats = await asyncio.to_thread(self.worktrees.diff_stats, worktree, base)
        diff = await asyncio.to_thread(self.worktrees.diff_text, worktree, base, "HEAD", 0)
        findings = check_diff(
            stats,
            diff,
            spec,
            case_insensitive=paths.is_case_insensitive(worktree),
            project_files=ctx.is_setup,
        )
        for finding in findings:
            ctx.journal.append("verify.finding", finding.to_dict())
        # A setup card is proven against the project file it proposes, on the project's own
        # database (no card branch exists for a database it is only now declaring).
        environ = self._environ(ctx.project_slug)
        gate_spec, gate_env = spec, _database_env(spec, ctx.card_id, environ)
        proposal_problem: str | None = None
        if ctx.is_setup:
            proposed, proposal_problem = await asyncio.to_thread(proposed_spec, worktree)
            if proposed is not None:
                gate_spec = proposed
                gate_env = checkout_variables(
                    self.settings.home, ctx.project_slug, list(proposed.workspace.database)
                )
                # A database the project already declared has a card branch: prove the
                # proposal there, never on the default branch the person's checkout uses.
                gate_env.update(
                    await asyncio.to_thread(_branch_env, proposed, ctx.card_id, environ)
                )
        timeout = min(gate_spec.verify.timeout_seconds, self.settings.gate_timeout_seconds)
        gates = []
        if not findings and proposal_problem is not None:
            gates = [problem_gate(proposal_problem)]
        elif not findings:
            if ctx.is_setup:
                # A proposed setup must work from a fresh clone, not on top of what the agent
                # installed while trying things: a stale .venv or node_modules can make its
                # gates pass, or fail, for reasons the next checkout will not share.
                await asyncio.to_thread(_reset_worktree, worktree, ignored=True)
            if not ctx.is_setup:
                head_tree = await asyncio.to_thread(speculative.tree_of, worktree, stats.head)
                for key, review in active.speculative.values():
                    if key == head_tree and active.early_review is None:
                        active.early_review = (key, review)
                    else:
                        review.cancel()
                active.speculative.clear()
                if active.early_review is not None and active.early_review[0] == head_tree:
                    ctx.journal.append("critique.speculative_kept", {"tree": head_tree})
                else:
                    if active.early_review is not None:
                        active.early_review[1].cancel()
                        active.early_review = None
                    self._start_early_review(ctx, active, stats, diff, gate_spec, tree=head_tree)
            try:
                sandbox = await asyncio.to_thread(self._sandbox, ctx, gate_env, gate_spec)
            except SandboxError as exc:
                sandbox, gates = None, [sandbox_gate(str(exc))]
            if not gates:
                gates = await asyncio.to_thread(
                    functools.partial(
                        run_gates,
                        gate_spec,
                        worktree,
                        ctx.run_dir,
                        stop=active.stop,
                        timeout=timeout,
                        env=gate_env or None,
                        sandbox=sandbox,
                    )
                )
            failed = [g for g in gates if not g.passed and not g.cancelled]
            if ctx.is_setup and failed and _uses_project_database(gate_spec):
                retried = await self._try_fallback(ctx, gate_spec, active, timeout, failed)
                if retried is not None:
                    gates, gate_env = retried
            if ctx.is_setup and all(g.passed for g in gates):
                checks = await asyncio.to_thread(
                    functools.partial(
                        check_environments,
                        EnvironmentManager(
                            self.settings.home, environment_sandboxes(self.settings)
                        ),
                        ctx.project_slug,
                        gate_spec,
                        worktree,
                        ctx.card_id,
                        gate_env,
                        stop=active.stop.is_set,
                    )
                )
                gates += [check.as_gate() for check in checks]
            if ctx.is_setup and not any(g.cancelled for g in gates):
                leftover = await asyncio.to_thread(leftover_gate, worktree)
                if leftover is not None:
                    gates = [*gates, leftover]
            if sandbox is not None:
                await asyncio.to_thread(sandbox.cleanup)
                problem = await asyncio.to_thread(sandbox.tampered)
                if problem is not None:
                    gates = [*gates, sandbox_gate(f"a container changed what git reads: {problem}")]
            # Gates must not leave changes behind that a later commit would pick up.
            await asyncio.to_thread(_reset_worktree, worktree)
        for gate in gates:
            ctx.journal.append("verify.gate", gate.to_dict())
            if gate.cancelled:
                raise asyncio.CancelledError
            if gate.exit_code is None and not gate.timed_out:
                # The command never ran: a missing tool or a broken project file,
                # not a verdict on the work.
                record_error(
                    self.db,
                    "gate",
                    "gate_not_started",
                    gate.summary,
                    detail={
                        "gate": gate.name,
                        "command": gate.command,
                        "run_id": ctx.run_id,
                        "card_id": ctx.card_id,
                    },
                )
        changes = classify_diff(stats, diff, spec.changes)
        databases: list[dict[str, Any]] = []
        for project_db in _databases(spec, environ):
            tables = await asyncio.to_thread(project_db.changes, ctx.card_id)
            databases.append(
                {
                    "name": project_db.spec.name,
                    "database": project_db.database,
                    "branch": branch_name(ctx.card_id),
                    # Bound like the git head: approve refuses if the branch moves.
                    "head": await asyncio.to_thread(project_db.head, ctx.card_id),
                    "tables": [table.to_dict() for table in tables],
                }
            )
        detail = {
            # The evidence below was computed for this commit and no other.
            "head_commit": stats.head,
            "findings": [f.to_dict() for f in findings],
            "gates": [g.to_dict() for g in gates],
            "changes": _with_data_changes(changes.to_dict(), databases, spec),
            "databases": databases,
        }
        failed = [g for g in gates if not g.passed]
        if findings or failed:
            for _, review in active.speculative.values():
                review.cancel()
            active.speculative.clear()
        if (findings or failed) and active.early_review is not None:
            active.early_review[1].cancel()
            active.early_review = None
        if findings:
            return StepResult(
                StepStatus.FAILED,
                detail,
                RunStatus.FAILED,
                f"verification failed: {findings[0].message}",
                findings[0].message,
            )
        if failed:
            return StepResult(
                StepStatus.FAILED,
                detail,
                RunStatus.FAILED,
                f"verification failed: {failed[0].summary}",
                failed[0].summary,
            )
        return StepResult(detail=detail)

    async def _review_request(
        self,
        ctx: _Ctx,
        stats: Any,
        diff: str,
        gates: list[dict[str, Any]],
        changes: Any,
        databases: list[dict[str, Any]],
        model: str | None,
    ) -> tuple[ReviewRequest, Any]:
        remaining = self._remaining_budget(ctx)
        # The diff's file names seed the code map too: the reviewer needs notes on what changed.
        knowledge = await self._knowledge(ctx, Role.CRITIC, diff[:4000])
        request = ReviewRequest(
            card_id=ctx.card_id,
            title=ctx.title,
            goal=ctx.goal,
            acceptance_criteria=ctx.acceptance_criteria,
            diff=diff[:MAX_REVIEW_DIFF_CHARS],
            diff_stats=stats.to_dict(),
            gates=gates,
            journal_path=str(ctx.journal.path),
            summary=render_summary(stats),
            workdir=str(ctx.worktree_path),
            policy=self._guarded(ctx, Autonomy.READ_ONLY, "critic"),
            model=model,
            budget_usd=remaining,
            feedback=ctx.feedback,
            on_event=self._journal_events(ctx, "critic"),
            changes=changes if isinstance(changes, dict) else None,
            head_commit=stats.head,
            databases=databases or None,
            knowledge="\n\n".join(
                t
                for t in (
                    knowledge.text,
                    await asyncio.to_thread(self._bench_memory, ctx, "critic"),
                )
                if t
            ),
        )
        return request, knowledge

    def _start_early_review(
        self,
        ctx: _Ctx,
        active: _Active,
        stats: Any,
        diff: str,
        spec: ProjectSpec,
        *,
        tree: str | None = None,
        worktree: Path | None = None,
        slot: str | None = None,
    ) -> None:
        """Start the reviewer while the gates run (``review_during_gates``). Its verdict is used
        only if every gate then passes on the same commit; a failing gate discards it. Cards with
        database changes are reviewed after the gates, which they need to show those changes."""
        if (
            self.reviewer is None
            or not self.settings.review_during_gates
            or not spec.verify.gates
            or spec.workspace.database
        ):
            return
        remaining = self._remaining_budget(ctx)
        if remaining is not None and remaining <= 0:
            return
        pending: list[dict[str, Any]] = []
        for name in spec.verify.gates:
            command = spec.commands.get(name)
            pending.append(
                {
                    "name": name,
                    "command": command_text(command) if command is not None else "",
                    "pending": True,
                }
            )
        changes = classify_diff(stats, diff, spec.changes).to_dict()
        model = self._model(Role.CRITIC, ctx)
        reviewer = self.reviewer

        # The reviewer reads the worktree the snapshot came from (a racing builder's, maybe).
        review_ctx = dataclasses.replace(ctx, worktree=worktree) if worktree else ctx

        async def review() -> tuple[ReviewRequest, Any, Any]:
            request, knowledge = await self._review_request(
                review_ctx, stats, diff, pending, changes, [], model
            )
            return request, knowledge, await reviewer.review(request)

        key = tree or speculative.tree_of(ctx.worktree_path, stats.head) or stats.head
        ctx.journal.append("critique.early", {"head_commit": stats.head, "tree": key})
        started = (key, asyncio.create_task(review(), name="prat-early-review"))
        if slot is not None:
            active.speculative[slot] = started
        else:
            active.early_review = started

    async def _step_critique(self, ctx: _Ctx, active: _Active) -> StepResult:
        if self.reviewer is None:
            return StepResult(StepStatus.SKIPPED, detail={"reason": "no reviewer configured"})
        remaining = self._remaining_budget(ctx)
        if remaining is not None and remaining <= 0:
            return StepResult(
                StepStatus.SKIPPED, detail={"reason": "no budget left for the reviewer"}
            )
        stats = await asyncio.to_thread(self.worktrees.diff_stats, ctx.worktree_path, ctx.base)
        diff = await asyncio.to_thread(self.worktrees.diff_text, ctx.worktree_path, ctx.base)
        with self.db.session() as session:
            verify = self._step_row(session, ctx.run_id, StepName.VERIFY)
            gates = list(verify.detail.get("gates", []))
            changes = verify.detail.get("changes")
            databases = list(verify.detail.get("databases") or [])
            project = session.get(Project, ctx.project_id)
            is_self = bool(project is not None and project.is_self)
        wants_security = is_self or "security" in ctx.project_spec.review.reviewers
        model = self._model(Role.CRITIC, ctx)
        early, active.early_review = active.early_review, None
        reused: tuple[ReviewRequest, Any, Any] | None = None
        head_tree = await asyncio.to_thread(speculative.tree_of, ctx.worktree_path, stats.head)
        if early is not None and early[0] in (head_tree, stats.head):
            try:
                reused = await early[1]
            except Exception:
                log.exception("the early review of run %s failed; reviewing again", ctx.run_id)
        elif early is not None:
            early[1].cancel()
        if reused is not None:
            request, knowledge, result = reused
            # The verdict was reached while the gates ran; they have all passed since.
            request = dataclasses.replace(request, gates=gates)
        else:
            request, knowledge = await self._review_request(
                ctx, stats, diff, gates, changes, databases, model
            )
            result = await self.reviewer.review(request)
        if result.cost_usd:
            self._add_cost(ctx.run_id, result.cost_usd)
        verdict_word = str(result.verdict)
        await asyncio.to_thread(
            self._bench_episode,
            ctx,
            "critic",
            f"Reviewed it and said {verdict_word}: {result.summary}",
            memory.Feeling(
                "confident" if verdict_word == "met" else "concerned",
                0.4 if verdict_word == "met" else -0.2,
                0.4,
            ),
        )

        security: SecurityResult | None = None
        if wants_security and not result.needs_rework:
            security = await self._security_review(ctx, request)
        concern = security is not None and not security.passed
        detail: dict[str, Any] = {
            **result.to_dict(),
            "model": model,
            "rework": result.needs_rework or concern,
            "head_commit": stats.head,
            "security_required": wants_security,
        }
        if knowledge.ids:
            detail["knowledge"] = knowledge.ids
        if security is not None:
            detail["security"] = {**security.to_dict(), "head_commit": stats.head}
        with self.db.session() as session:
            card = store.get_card(session, ctx.card_id)
            store.add_comment(
                session,
                card,
                result.render(),
                author=CommentAuthor.REVIEWER,
                kind="verdict",
                run_id=ctx.run_id,
                data=detail,
            )
            if security is not None:
                store.add_comment(
                    session,
                    card,
                    security.render(),
                    author=CommentAuthor.REVIEWER,
                    kind="security",
                    run_id=ctx.run_id,
                    data=detail["security"],
                )
            if result.needs_rework:
                # The card goes back to the builder (applied by finalize), not to review.
                store.add_comment(
                    session,
                    card,
                    result.render_fix_list(),
                    author=CommentAuthor.REVIEWER,
                    kind="fix_list",
                    run_id=ctx.run_id,
                    data={"verdict": str(result.verdict), "fix_list": list(result.fix_list)},
                )
            elif security is not None and concern:
                store.add_comment(
                    session,
                    card,
                    security.render_fix_list(),
                    author=CommentAuthor.REVIEWER,
                    kind="fix_list",
                    run_id=ctx.run_id,
                    data={
                        "verdict": "security_concern",
                        "fix_list": [f.issue for f in security.findings],
                    },
                )
        return StepResult(detail=detail)

    async def _security_review(self, ctx: _Ctx, request: ReviewRequest) -> SecurityResult:
        """The second review. Missing reviewer, budget or answer all count as a concern."""
        if self.security_reviewer is None:
            return SecurityResult(
                SecurityVerdict.CONCERN, "No security reviewer is configured.", parsed=False
            )
        remaining = self._remaining_budget(ctx)
        if remaining is not None and remaining <= 0:
            return SecurityResult(
                SecurityVerdict.CONCERN, "No budget was left for the security review.", parsed=False
            )
        security = await self.security_reviewer.review(
            dataclasses.replace(
                request,
                policy=self._guarded(ctx, Autonomy.READ_ONLY, "security_reviewer"),
                model=self._model(Role.SECURITY_REVIEWER, ctx),
                budget_usd=remaining,
                on_event=self._journal_events(ctx, "security_reviewer"),
            )
        )
        if security.cost_usd:
            self._add_cost(ctx.run_id, security.cost_usd)
        return security

    async def _step_summarize(self, ctx: _Ctx, active: _Active) -> StepResult:
        stats = await asyncio.to_thread(self.worktrees.diff_stats, ctx.worktree_path, ctx.base)
        summary = render_summary(stats)
        with self.db.session() as session:
            run = _get_run(session, ctx.run_id)
            run.summary = summary
            run.head_commit = stats.head
            run.card.summary = summary
        return StepResult(detail=stats.to_dict(), outcome=RunStatus.SUCCEEDED)

    async def _step_finalize(self, ctx: _Ctx, active: _Active) -> StepResult:
        detail: dict[str, Any] = {"warnings": []}
        if ctx.worktree is not None and ctx.worktree.exists():
            async with self._project_lock(ctx.project_id):
                try:
                    commit = await asyncio.to_thread(
                        self.worktrees.safety_commit,
                        ctx.worktree,
                        f"PRAT: save work from run {ctx.run_id} (card {ctx.card_id})",
                    )
                    detail["safety_commit"] = commit
                except Exception as exc:  # keep going: cleanup must not strand the card
                    detail["warnings"].append(f"safety commit failed: {exc}")
                try:
                    await asyncio.to_thread(
                        self.worktrees.remove_worktree, ctx.agent_path, ctx.worktree
                    )
                except Exception as exc:
                    detail["warnings"].append(f"worktree cleanup failed: {exc}")
        detail.update(self._apply_outcome(ctx.run_id))
        if detail.get("card_status") == str(CardStatus.REVIEW) and self._is_swarm_part(ctx.card_id):
            # A swarm part is merged into its epic's branch once this run has ended.
            self._after_run(ctx.card_id, functools.partial(self._swarm_part_finished, ctx.card_id))
        return StepResult(detail=detail)

    def _apply_outcome(self, run_id: int) -> dict[str, Any]:
        with self.db.session() as session:
            run = _get_run(session, run_id)
            outcome = run.outcome or RunStatus.FAILED
            if run.status == RunStatus.RUNNING:
                store.set_run_status(session, run, outcome, run.reason)
                run.finished_at = _now()
            target = OUTCOME_TO_CARD[outcome]
            reason = None if target == CardStatus.REVIEW else run.reason
            if outcome == RunStatus.CANCELLED and run.stop_only:
                target, reason = CardStatus.FAILED, STOPPED_REASON
                run.reason = STOPPED_REASON
            if outcome == RunStatus.SUCCEEDED:
                critique = next((s for s in run.steps if s.name == StepName.CRITIQUE), None)
                if critique is not None and (critique.detail or {}).get("rework"):
                    target = CardStatus.REWORK
                    security = critique.detail.get("security") or {}
                    if security.get("verdict") == str(SecurityVerdict.CONCERN):
                        reason = "security review raised a concern: sent back"
                    else:
                        reason = f"reviewer verdict {critique.detail.get('verdict')}: sent back"
            card = run.card
            if card.status != target:
                store.set_card_status(session, card, target, reason)
            return {"run_status": str(run.status), "card_status": str(card.status)}


# Helpers ----------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _need[T](value: T | None, what: str) -> T:
    if value is None:
        raise EngineError(f"run is missing its {what}; the prepare step did not complete")
    return value


def _get_run(session: Any, run_id: int) -> Run:
    run = session.get(Run, run_id)
    if not isinstance(run, Run):
        raise EngineError(f"no run {run_id}")
    return run


async def _undo_data(merged: list[tuple[DoltBranchProvider, str, str]]) -> None:
    """Undo database merges, newest first. Best effort: each is logged if it fails."""
    for project_db, before, _after in reversed(merged):
        try:
            await asyncio.to_thread(project_db.undo_merge, before)
        except Exception:
            log.exception("could not undo the merge into %s", project_db.database)


def _databases(
    spec: ProjectSpec, environ: Mapping[str, str] | None = None
) -> list[DoltBranchProvider]:
    """The project's declared databases, their URL variables looked up in ``environ`` (PRAT's
    environment with this machine's variables for the project). Raises WorkspaceError when one
    cannot be reached because its URL variable is missing."""
    if not spec.workspace.database:
        return []
    return providers_for(spec.workspace.database, environ=environ)


def _card_text(ctx: _Ctx) -> str:
    """The card's own goal and criteria, after the setup agent's instructions."""
    return (
        f"\n# This card: {ctx.title}\n\n{ctx.goal}\n\n## Acceptance criteria\n"
        f"{ctx.acceptance_criteria}\n"
    )


def _database_env(
    spec: ProjectSpec, card_id: int, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    env: dict[str, str] = {}
    for project_db in _databases(spec, environ):
        env.update(project_db.agent_env(card_id))
    return env


def _branch_env(
    spec: ProjectSpec, card_id: int, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """``_database_env`` for the databases that already have a branch for this card."""
    env: dict[str, str] = {}
    try:
        project_dbs = _databases(spec, environ)
    except WorkspaceError:
        return env
    for project_db in project_dbs:
        with contextlib.suppress(WorkspaceError, SQLAlchemyError):
            if project_db.exists(card_id):
                env.update(project_db.agent_env(card_id))
    return env


def _with_data_changes(
    changes: dict[str, Any], databases: list[dict[str, Any]], spec: ProjectSpec
) -> dict[str, Any]:
    """Add database changes to the change classification: a schema change is high
    risk, a data change medium; a project can reserve either for a person."""
    tables = [table for database in databases for table in database["tables"]]
    if not tables:
        return changes
    categories = dict(changes.get("categories") or {})
    flags = list(changes.get("flags") or [])
    require = list(changes.get("require_person") or [])
    if any(table["schema_change"] for table in tables):
        categories["schema"] = "high"
        changes["risk"] = "high"
    if any(table["data_change"] for table in tables):
        categories.setdefault("data", "medium")
        if changes.get("risk") == "low":
            changes["risk"] = "medium"
    for category in ("schema", "data"):
        reserved = category in categories and category in spec.changes.require_person
        if reserved and category not in require:
            require.append(category)
            flags.append(f"needs a person's approval: {category}")
    flags.append(
        "database changes: "
        + ", ".join(
            f"{t['table']} (+{t['rows_added']} ~{t['rows_modified']} -{t['rows_deleted']}"
            f"{', schema' if t['schema_change'] else ''})"
            for t in tables
        )
    )
    changes.update(categories=dict(sorted(categories.items())), flags=flags, require_person=require)
    return changes


def _spec_of(project: Project) -> ProjectSpec:
    data = dict(project.config or {})
    data.setdefault("name", project.name)
    data.setdefault("default_branch", project.default_branch)
    return ProjectSpec.model_validate(data)


def _spec_from_branch(agent_path: Path, branch: str) -> ProjectSpec | None:
    """The project file as committed on the default branch, if there is one."""
    result = show_project_file(Git(agent_path), branch)
    if not result.ok:
        return None
    return parse_spec(result.stdout, source=f"{branch}:{PROJECT_FILE_REL}")


def _reset_worktree(worktree: Path, *, ignored: bool = False) -> None:
    """Back to the commit. With ``ignored``, ignored files go too (.venv, node_modules,
    vendor), so what runs next starts from what a fresh clone has."""
    git = Git(worktree)
    git.run("reset", "--hard", "--quiet", "HEAD")
    git.run("clean", "-ffdx" if ignored else "-fd", "--quiet")


def _plan_from(run: Run) -> str | None:
    for step in run.steps:
        if step.name == StepName.PLAN and step.status == StepStatus.SUCCEEDED:
            plan = step.detail.get("plan")
            return plan if isinstance(plan, str) else None
    return None


def _follow_checkout(checkout: Path, agent: Path, default: str, projects_dir: Path) -> str:
    """Bring approved work into a checkout PRAT cloned itself (a project added from a link),
    so its environments run what was approved. A person's own clone is never touched, nor a
    clone PRAT made that has local changes or another branch checked out."""
    if not paths.is_within(checkout, projects_dir) or not (checkout / ".git").exists():
        return "not PRAT's"
    git = Git(checkout)
    if git.current_branch() != default or git.is_dirty():
        return "skipped: changes or another branch checked out"
    fetched = git.run("fetch", "--quiet", str(agent), default, check=False)
    if not fetched.ok:
        return f"skipped: {fetched.stderr.strip()[:200]}"
    merged = git.run("merge", "--ff-only", "--quiet", "FETCH_HEAD", check=False)
    return "updated" if merged.ok else "skipped: diverged"


def _uses_project_database(spec: ProjectSpec) -> bool:
    """Whether a proposed project file uses the database PRAT made for the project."""
    return any(db.url_env == DATABASE_VARIABLE for db in spec.workspace.database)
