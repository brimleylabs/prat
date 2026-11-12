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
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from prat import paths, store
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
    repository_instructions,
)
from prat.engine.security import SecurityResult, SecurityReviewer, SecurityVerdict
from prat.engine.summary import render_summary
from prat.environments import (
    EnvironmentManager,
    checkout_variables,
    database_environ,
    load_local,
)
from prat.errors import record_error
from prat.git import Git, MergeConflict, WorktreeManager, card_branch
from prat.journal import Journal
from prat.knowledge.provide import for_card
from prat.knowledge.select import Selection
from prat.model_registry import Role, resolve_model
from prat.overlay import load_overlay, overlay_path
from prat.policy import Decision, McpToolSpec, Policy, ToolCall
from prat.projects import PROJECT_FILE_REL, ProjectSpec, command_argv, parse_spec, show_project_file
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
    event_to_dict,
)
from prat.runtime.loop import LoopDetector
from prat.sandbox import Sandbox, SandboxError, environment_sandboxes
from prat.sandbox import for_settings as sandbox_for
from prat.setup_agent import (
    DATABASE_VARIABLE,
    SETUP_TASK,
    check_environments,
    fallback_database,
    fallback_gate,
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
commands. Run git commands on their own (not combined with other commands): they run on this
machine, where the repository's git data is writable.
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
    # A setup card's agent proposes the project file and its proposal is what verify proves.
    card_kind: CardKind = CardKind.CHANGE

    @property
    def is_setup(self) -> bool:
        return self.card_kind is CardKind.SETUP

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
        self._project_locks: dict[int, asyncio.Lock] = {}
        self._active: dict[int, _Active] = {}
        # Set by suspend(): a cancelled task is a restart, not a person cancelling.
        self._suspending = False
        self._tasks: dict[int, asyncio.Task[RunResult]] = {}
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
            async with self.limiter.slot(project_id):
                run_id = self._start_run(card_id)
                return await self._drive(run_id, card_id)
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
            if not card.branch:
                raise ApproveError(f"card {card_id} has no branch")
            project = card.project
            agent = Path(project.agent_path)
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
        return MergeResult(card_id, branch, commit)

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
            store.set_card_status(session, card, CardStatus.REWORK, "sent back for rework")
        versioning.commit(self.db, f"card {card_id} sent back for rework", actor="person")

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
        return self._result(run_id)

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
            card = store.get_card(session, ctx.card_id)
            branch = card.branch or card_branch(card.id, card.title)
        async with self._project_lock(ctx.project_id):
            if is_self:
                # PRAT's own clone is both checkout and agent clone (prat.improve).
                clone = await asyncio.to_thread(
                    self.worktrees.refresh_agent_clone,
                    ctx.agent_path,
                    ctx.default_branch,
                    source="the self repository's remote",
                )
            else:
                clone = await asyncio.to_thread(
                    self.worktrees.prepare_agent_clone,
                    ctx.checkout_path,
                    ctx.agent_path,
                    ctx.default_branch,
                )
            spec = _spec_from_branch(ctx.agent_path, ctx.default_branch) or fallback_spec
            # The installation's overlay only adds to the project's rules; the run
            # records the result, so a resumed run keeps the rules it started with.
            overlay = load_overlay(overlay_path(self.settings.home))
            spec = overlay.apply_to_spec(spec)
            path = self.worktrees.worktree_path(ctx.project_slug, ctx.card_id)
            paths.assert_outside(path, all_checkouts)
            info = await asyncio.to_thread(
                self.worktrees.create_worktree, ctx.agent_path, path, branch, ctx.default_branch
            )
            # A rerun's branch may predate the project file it now needs (a no-op otherwise).
            brought = await asyncio.to_thread(
                self.worktrees.bring_project_files, info.path, ctx.default_branch
            )
            if brought == "merged":
                info = replace(info, head=Git(info.path).head())
            elif brought == "conflict":
                clone.warnings.append(
                    f"{ctx.default_branch} changed the project file, but merging it into "
                    f"{branch} conflicted; the card runs with the project file it had"
                )
            base = await asyncio.to_thread(
                self.worktrees.merge_base, ctx.agent_path, ctx.default_branch, branch
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

        return record

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

    async def _step_plan(self, ctx: _Ctx, active: _Active) -> StepResult:
        remaining = self._remaining_budget(ctx)
        knowledge = await self._knowledge(ctx, Role.PLANNER)
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
                knowledge=knowledge.text,
            )
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
        if ctx.is_setup:
            variables = load_local(self.settings.home, ctx.project_slug).variables
            env = {**variables, **env}
            system_prompt = setup_system_prompt(
                name=ctx.project_spec.name,
                default_branch=ctx.default_branch,
                autonomy=ctx.run_autonomy,
                database=DATABASE_VARIABLE in variables,
                repo_instructions=repository_instructions(instructions),
            ) + _card_text(ctx)
            task_prompt = builder_task_prompt(feedback=ctx.feedback, plan=ctx.plan).replace(
                "Implement the card described in your instructions.", SETUP_TASK, 1
            )
        else:
            system_prompt = builder_system_prompt(
                title=ctx.title,
                goal=ctx.goal,
                acceptance_criteria=ctx.acceptance_criteria,
                spec=ctx.project_spec,
                autonomy=ctx.run_autonomy,
                repo_instructions=instructions,
                knowledge=knowledge.text,
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
        )
        journal.append(
            "execute.model",
            {
                "model": model,
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
        try:
            async for event in stream:
                journal.append(f"runtime.{event.type}", event_to_dict(event))
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
            if sandbox is not None:
                # Commands the agent's runtime gave up on leave their containers behind.
                await asyncio.to_thread(sandbox.cleanup)

        detail: dict[str, Any] = {
            "session_id": active.session_id,
            "model": model,
            # This step's own spend; the run row holds the running total.
            "cost_usd": round(cost - spent_before, 6),
            "outcome": str(outcome) if outcome else None,
            "message": message,
        }
        if knowledge.ids:
            detail["knowledge"] = knowledge.ids
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
            f"PRAT: save work from run {ctx.run_id} (card {ctx.card_id})",
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
        if empty is not None and ctx.feedback:
            # A rework may only have to show something (run a check, answer a question): the
            # card's earlier work is on the branch, and that is what verify and review judge.
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
        timeout = min(gate_spec.verify.timeout_seconds, self.settings.gate_timeout_seconds)
        gates = []
        if not findings and proposal_problem is not None:
            gates = [problem_gate(proposal_problem)]
        elif not findings:
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
            if sandbox is not None:
                await asyncio.to_thread(sandbox.cleanup)
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
        if findings:
            return StepResult(
                StepStatus.FAILED,
                detail,
                RunStatus.FAILED,
                f"verification failed: {findings[0].message}",
                findings[0].message,
            )
        failed = [g for g in gates if not g.passed]
        if failed:
            return StepResult(
                StepStatus.FAILED,
                detail,
                RunStatus.FAILED,
                f"verification failed: {failed[0].summary}",
                failed[0].summary,
            )
        return StepResult(detail=detail)

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
            knowledge=knowledge.text,
        )
        result = await self.reviewer.review(request)
        if result.cost_usd:
            self._add_cost(ctx.run_id, result.cost_usd)

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


def _reset_worktree(worktree: Path) -> None:
    git = Git(worktree)
    git.run("reset", "--hard", "--quiet", "HEAD")
    git.run("clean", "-fd", "--quiet")


def _plan_from(run: Run) -> str | None:
    for step in run.steps:
        if step.name == StepName.PLAN and step.status == StepStatus.SUCCEEDED:
            plan = step.detail.get("plan")
            return plan if isinstance(plan, str) else None
    return None


def _uses_project_database(spec: ProjectSpec) -> bool:
    """Whether a proposed project file uses the database PRAT made for the project."""
    return any(db.url_env == DATABASE_VARIABLE for db in spec.workspace.database)
