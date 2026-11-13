"""Run evaluation cases through the whole PRAT pipeline, each in a home of its own.

Every case gets a fresh PRAT home (its own SQLite database, journals and worktrees), a
fresh git repository made from the case's starting files and its own agent clone. Nothing
an evaluation does reaches a real project, card, branch or database: a case whose project
file declares databases is refused.

A case's card runs the way any card does. When the reviewer sends it back, it runs again
with the fix list; when the gates fail, it is sent back with their output, the way a
person would send it back, until it reaches review or runs out of attempts. After every
run the hidden check judges the commit the run left on the card's branch, so each attempt
is scored whether or not PRAT handed it over (prat.evals.results).
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from prat import __version__, store
from prat.config import Settings
from prat.db import Database, Run
from prat.db.migrate import upgrade
from prat.domain import CardStatus
from prat.engine import (
    Engine,
    FakeSecurityReviewer,
    NoPlanner,
    Planner,
    Reviewer,
    ReviewRequest,
    ReviewResult,
    SecurityReviewer,
    SecurityVerdict,
)
from prat.evals.cases import CaseError, EvalCase, fill_python
from prat.evals.results import (
    Attempt,
    CaseResult,
    CheckOutcome,
    EvalResult,
    GateOutcome,
    PolicyCount,
    evals_dir,
    new_id,
    save,
)
from prat.git import Git
from prat.git.worktree import rmtree
from prat.journal import read_journal
from prat.overlay import OverlayGuidedRuntime, overlay_path
from prat.projects import PROJECT_FILE_REL, ProjectSpec, load_spec
from prat.runtime import AgentRuntime, AgentTask, FakeRuntime, FakeScript, FakeStep
from prat.sandbox import for_settings
from prat.verification import gate_environment, run_command

Who = Literal["scripted", "claude"]
ClaudeFactory = Callable[[Settings], AgentRuntime]
Progress = Callable[[CaseResult], None]

OUTPUT_CHARS = 1500
REPO_AUTHOR = ("PRAT evaluation", "eval@prat.invalid")


@dataclass(frozen=True)
class EvalOptions:
    # Who builds: the case's scripted attempts, or a real agent.
    runtime: Who = "scripted"
    # Who reviews: the case's scripted verdicts, or the real reviewers.
    reviewer: Who = "scripted"
    # Overrides each case's max_attempts.
    max_attempts: int | None = None
    label: str = ""
    # Use this installation's overlay (its rules and guidance) in every case.
    overlay: bool = True
    # Keep each case's scratch home, repository and agent clone after the run.
    keep_work: bool = False


class ScriptedReviewer:
    """The case's scripted verdicts, one per review, in order."""

    def __init__(self, case: EvalCase) -> None:
        self.case = case
        self.reviews = 0

    async def review(self, request: ReviewRequest) -> ReviewResult:
        scripted = self.case.review_for(self.reviews)
        self.reviews += 1
        return ReviewResult(
            scripted.verdict,
            scripted.summary,
            list(scripted.concerns),
            confidence=1.0,
            fix_list=list(scripted.fix_list),
        )


def builder_script(case: EvalCase) -> Callable[[AgentTask], FakeScript]:
    """The scripted builder: the case's first attempt on the card's first run, the second
    on the next, and so on. Any other role has nothing to do."""
    runs: list[str] = []
    idle = FakeScript(steps=[FakeStep(op="finish", message="nothing to do")])

    def script(task: AgentTask) -> FakeScript:
        if task.metadata.get("role", "builder") != "builder" or not case.attempts:
            return idle
        run_id = task.metadata.get("run_id", "")
        if run_id not in runs:
            runs.append(run_id)
        return case.attempts[min(runs.index(run_id), len(case.attempts) - 1)]

    return script


async def run_suite(
    settings: Settings,
    cases: list[EvalCase],
    options: EvalOptions,
    *,
    suite: str,
    claude: ClaudeFactory | None = None,
    progress: Progress | None = None,
) -> EvalResult:
    """Run every case, store the result under ``$PRAT_HOME/evals/<id>/`` and return it."""
    eval_id = new_id(options.label)
    folder = evals_dir(settings.home) / eval_id
    work = folder / "work"
    started = time.monotonic()
    results: list[CaseResult] = []
    try:
        for case in cases:
            case_result = await run_case(
                settings,
                case,
                options,
                work / case.name,
                claude=claude,
                journals=folder / "journals" / case.name,
            )
            results.append(case_result)
            if progress is not None:
                progress(case_result)
    finally:
        if not options.keep_work and work.exists():
            await asyncio.to_thread(rmtree, work)
    result = EvalResult(
        id=eval_id,
        label=options.label,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        prat_version=__version__,
        suite=suite,
        runtime=options.runtime,
        reviewer=options.reviewer,
        sandbox=settings.sandbox,
        overlay=options.overlay and overlay_path(settings.home).is_dir(),
        models=dict(settings.models),
        duration_seconds=round(time.monotonic() - started, 1),
        cases=results,
    )
    save(result, folder)
    return result


async def run_case(
    settings: Settings,
    case: EvalCase,
    options: EvalOptions,
    work: Path,
    *,
    claude: ClaudeFactory | None = None,
    journals: Path | None = None,
) -> CaseResult:
    """Run one case in ``work``. A case that cannot run reports an error; it never ends
    the evaluation."""
    result = CaseResult(
        name=case.name,
        title=case.spec.title,
        has_check=case.spec.check is not None,
        recorded=list(case.spec.recorded.runs) if case.spec.recorded else None,
    )
    if options.runtime == "scripted" and not case.attempts:
        result.error = "no scripted attempts (attempts/1.json); run it with --runtime claude"
        return result
    try:
        await _run(settings, case, options, work, result, claude, journals)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # one broken case must not end the evaluation
        result.error = f"{type(exc).__name__}: {exc}"
    return result


async def _run(
    settings: Settings,
    case: EvalCase,
    options: EvalOptions,
    work: Path,
    result: CaseResult,
    claude: ClaudeFactory | None,
    journals: Path | None,
) -> None:
    sandboxed = settings.sandbox == "docker"
    scratch, checkout, spec = await asyncio.to_thread(
        _prepare, settings, case, work, sandboxed, options.overlay
    )
    result.project = spec.name
    agent_clone = work / "agent"
    db = Database(scratch.db_url)
    try:
        upgrade(db)
        with db.session() as session:
            project = store.add_project(
                session, slug=case.name, checkout_path=checkout, agent_path=agent_clone, spec=spec
            )
            card_id = store.add_card(
                session,
                project,
                title=case.spec.title,
                goal=case.spec.goal,
                acceptance_criteria=case.spec.acceptance_criteria,
                complexity=case.spec.complexity,
                ready=True,
            ).id
        engine = make_engine(scratch, db, case, options, claude)
        limit = options.max_attempts or case.spec.max_attempts
        if options.runtime == "scripted":
            limit = min(limit, len(case.attempts))
        for number in range(1, limit + 1):
            started = time.monotonic()
            outcome = await engine.run_card(card_id)
            attempt = read_attempt(db, scratch, outcome.run_id, number)
            attempt.card_status = str(outcome.card_status)
            attempt.reason = outcome.reason
            attempt.duration_seconds = round(time.monotonic() - started, 2)
            if case.spec.check is not None and attempt.commit is not None:
                attempt.check = await asyncio.to_thread(
                    hidden_check,
                    case,
                    scratch,
                    agent_clone,
                    attempt.commit,
                    work / f"check-{number}",
                    sandboxed,
                )
            result.attempts.append(attempt)
            if journals is not None and outcome.run_id is not None:
                keep_journal(scratch, outcome.run_id, journals / f"attempt-{number}.jsonl")
            if number == limit:
                break
            if outcome.card_status == CardStatus.REWORK:
                continue
            if outcome.card_status == CardStatus.FAILED and attempt.steps.get("verify") == "failed":
                engine.rework(card_id, gate_feedback(attempt))
                continue
            break
    finally:
        db.dispose()


def _prepare(
    settings: Settings, case: EvalCase, work: Path, sandboxed: bool, overlay: bool
) -> tuple[Settings, Path, ProjectSpec]:
    """The case's own home and repository, made fresh."""
    if work.exists():
        rmtree(work)
    scratch = scratch_settings(settings, work / "home")
    scratch.ensure_dirs()
    if overlay:
        copy_overlay(settings.home, scratch.home)
    checkout = make_repo(case, work / "repo", sandboxed)
    spec = load_spec(checkout, default_name=case.name)
    if spec.workspace.database:
        raise CaseError("the project file declares databases; evaluations never use one")
    return scratch, checkout, spec


def scratch_settings(settings: Settings, home: Path) -> Settings:
    """This installation's settings for a home of the case's own: its own SQLite database,
    and no database server a card could reach."""
    return settings.model_copy(
        update={
            "home": home.resolve(),
            "database_url": None,
            "store": "sqlite",
            "projects_dir": home.resolve() / "projects",
            "project_database_url": None,
            "fallback_database_url": None,
        }
    )


def copy_overlay(home: Path, scratch_home: Path) -> None:
    source = overlay_path(home)
    if source.is_dir():
        shutil.copytree(source, overlay_path(scratch_home), ignore=shutil.ignore_patterns(".git"))


def make_repo(case: EvalCase, checkout: Path, sandboxed: bool) -> Path:
    """A git repository holding the case's starting files in one commit."""
    shutil.copytree(case.repo, checkout)
    project_file = checkout / PROJECT_FILE_REL
    if project_file.is_file():
        text = project_file.read_text(encoding="utf-8")
        project_file.write_text(fill_python(text, sandboxed), encoding="utf-8", newline="")
    branch = load_spec(checkout, default_name=case.name).default_branch
    git = Git(checkout)
    git.run("init", "--quiet", "--initial-branch", branch)
    git.config_set("user.name", REPO_AUTHOR[0])
    git.config_set("user.email", REPO_AUTHOR[1])
    git.config_set("core.autocrlf", "false")
    git.run("add", "--all")
    git.run("commit", "--quiet", "--no-verify", "-m", f"Starting point for {case.name}")
    return checkout


def make_engine(
    scratch: Settings,
    db: Database,
    case: EvalCase,
    options: EvalOptions,
    claude: ClaudeFactory | None,
) -> Engine:
    real: AgentRuntime | None = None
    if "claude" in (options.runtime, options.reviewer):
        if claude is None:
            raise CaseError("the claude runtime is not available here")
        real = OverlayGuidedRuntime(claude(scratch), scratch.home)
    builder: AgentRuntime
    planner: Planner
    reviewer: Reviewer
    security: SecurityReviewer
    if options.runtime == "claude" and real is not None:
        from prat.engine.planner import AgentPlanner

        builder, planner = real, AgentPlanner(real)
    else:
        fake = FakeRuntime(builder_script(case), state_dir=scratch.home / "fake-sessions")
        builder, planner = OverlayGuidedRuntime(fake, scratch.home), NoPlanner()
    if options.reviewer == "claude" and real is not None:
        from prat.engine.critic import AgentReviewer
        from prat.engine.security import AgentSecurityReviewer

        reviewer, security = AgentReviewer(real), AgentSecurityReviewer(real)
    else:
        reviewer, security = ScriptedReviewer(case), FakeSecurityReviewer()
    return Engine(
        scratch, db, builder, reviewer=reviewer, planner=planner, security_reviewer=security
    )


def read_attempt(db: Database, scratch: Settings, run_id: int | None, number: int) -> Attempt:
    """What the run did, from its record: steps, gates, verdict, cost and the commit it left
    on the card's branch."""
    attempt = Attempt(number=number, run_id=run_id, card_status="")
    if run_id is None:
        return attempt
    with db.session() as session:
        run = session.get(Run, run_id)
        if run is None:
            return attempt
        attempt.run_status = str(run.status)
        attempt.cost_usd = run.cost_usd or 0.0
        attempt.steps = {str(step.name): str(step.status) for step in run.steps}
        details = {str(step.name): dict(step.detail or {}) for step in run.steps}
        branch = run.branch or run.card.branch
        agent_clone = Path(run.card.project.agent_path)
        journal = Path(run.journal_path) if run.journal_path else None
    verify = details.get("verify", {})
    attempt.gates = [
        GateOutcome(
            name=str(gate.get("name") or ""),
            passed=bool(gate.get("passed")),
            output=str(gate.get("output_tail") or "")[-OUTPUT_CHARS:],
        )
        for gate in verify.get("gates") or []
    ]
    attempt.findings = [str(item.get("message") or "") for item in verify.get("findings") or []]
    critique = details.get("critique", {})
    if critique.get("verdict"):
        attempt.verdict = str(critique["verdict"])
        security = critique.get("security") or {}
        if security.get("verdict") == str(SecurityVerdict.CONCERN):
            attempt.verdict += ", security concern"
        attempt.cost_usd += float(critique.get("cost_usd") or 0.0)
    if branch:
        head = Git(agent_clone).run(
            "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}", check=False
        )
        attempt.commit = head.stdout.strip() or None
    attempt.policy = count_policy(journal or scratch.journal_dir / f"run-{run_id}.jsonl")
    return attempt


def count_policy(journal: Path) -> PolicyCount:
    """The builder's tool calls the policy decided on, and how many it denied."""
    count = PolicyCount()
    if not journal.is_file():
        return count
    for entry in read_journal(journal):
        if entry.kind == "policy" and entry.data.get("agent") == "builder":
            count.calls += 1
            if not entry.data.get("allowed"):
                count.denied += 1
    return count


def keep_journal(scratch: Settings, run_id: int, target: Path) -> None:
    source = scratch.journal_dir / f"run-{run_id}.jsonl"
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def gate_feedback(attempt: Attempt) -> str:
    """What a person sends back after failed checks: the failure and the gates' output."""
    lines = [
        "The checks failed, so the work was not handed over. Fix this and make them pass:",
        "",
        attempt.reason or "verification failed",
    ]
    for gate in attempt.gates:
        if not gate.passed and gate.output.strip():
            lines += ["", f"Output of {gate.name}:", gate.output.strip()]
    return "\n".join(lines)


def export_tree(repo: Path, commit: str, target: Path) -> None:
    """The files of ``commit``, without git, in ``target``."""
    data = Git(repo).run_bytes("archive", "--format=tar", commit)
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(target, filter="data")


def hidden_check(
    case: EvalCase,
    settings: Settings,
    agent_clone: Path,
    commit: str,
    folder: Path,
    sandboxed: bool,
) -> CheckOutcome:
    """Run the case's hidden check on ``commit`` with the hidden files laid over it. It runs
    code the agent wrote, so it runs the way gates do: in the sandbox when there is one,
    otherwise without PRAT's own settings and keys."""
    check = case.spec.check
    if check is None:
        raise CaseError("the case has no check")
    if folder.exists():
        rmtree(folder)
    tree = folder / "tree"
    export_tree(agent_clone, commit, tree)
    if case.hidden.is_dir():
        shutil.copytree(case.hidden, tree, dirs_exist_ok=True)
    argv = [fill_python(part, sandboxed) for part in check.command]
    log = folder / "check.log"
    box = for_settings(
        settings, name=f"eval-{case.name}-{os.urandom(3).hex()}", workdir=tree, network=False
    )
    try:
        code, timed_out, _, error = run_command(
            argv,
            tree,
            timeout=check.timeout_seconds,
            log_path=log,
            env={} if box is not None else gate_environment(),
            sandbox=box,
        )
    finally:
        if box is not None:
            box.cleanup()
    output = error or _read_tail(log)
    return CheckOutcome(
        passed=code == 0 and not timed_out,
        exit_code=code,
        timed_out=timed_out,
        output=output[-OUTPUT_CHARS:],
    )


def _read_tail(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-OUTPUT_CHARS * 4 :].decode("utf-8", errors="replace")
