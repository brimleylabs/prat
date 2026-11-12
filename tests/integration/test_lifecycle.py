"""Full card lifecycle through the engine with the scripted runtime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from conftest import WRITE_GREET, add_card, git, make_engine, project_toml
from prat import store
from prat.config import Settings
from prat.db import Card, Database, Project, Run
from prat.domain import (
    Autonomy,
    CardStatus,
    CommentAuthor,
    RunStatus,
    StepName,
    Verdict,
)
from prat.engine import ApproveError, Engine, FakeReviewer
from prat.journal import read_journal
from prat.runtime import (
    AgentTask,
    DoneEvent,
    FakeCrash,
    FakeRuntime,
    FakeScript,
    Outcome,
    RuntimeEvent,
    SessionStarted,
)

pytestmark = pytest.mark.integration


def card_state(db: Database, card_id: int) -> tuple[CardStatus, str | None]:
    with db.session() as session:
        card = store.get_card(session, card_id)
        return card.status, card.status_reason


def last_run(db: Database, card_id: int) -> Run:
    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None
        for step in run.steps:  # load while the session is open
            _ = step.status
        return run


def steps(run: Run) -> dict[str, str]:
    return {str(s.name): str(s.status) for s in run.steps}


def journal_kinds(run: Run) -> list[str]:
    assert run.journal_path
    return [e.kind for e in read_journal(Path(run.journal_path))]


def denials(run: Run) -> list[str]:
    assert run.journal_path
    return [
        e.data["reason"]
        for e in read_journal(Path(run.journal_path))
        if e.kind == "policy" and not e.data["allowed"]
    ]


def checkout_refs(project: Project) -> str:
    return git(Path(project.checkout_path), "for-each-ref", "--format=%(refname) %(objectname)")


async def wait_for(predicate: Callable[[], bool], timeout: float = 15.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not met")
        await asyncio.sleep(0.02)


# Happy path ------------------------------------------------------------------------


async def test_happy_path_to_review_then_approve(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project, goal="Add hello.greet()")
    reviewer = FakeReviewer(Verdict.COMPLETE, "Looks right.")
    engine, _runtime = make_engine(
        settings, db, [*WRITE_GREET, {"op": "cost", "usd": 0.4}], reviewer=reviewer
    )
    before = checkout_refs(project)

    result = await engine.run_card(card_id)

    assert result.run_status is RunStatus.SUCCEEDED
    assert result.card_status is CardStatus.REVIEW
    run = last_run(db, card_id)
    assert set(steps(run).values()) <= {"succeeded", "skipped"}
    assert steps(run)["plan"] == "skipped"
    assert run.branch == f"card/{card_id}-add-greet"
    assert run.summary and "hello.py" in run.summary and "+2 -0" in run.summary
    assert run.cost_usd == pytest.approx(0.0)  # the cost op comes after finish
    assert not Path(run.worktree_path or "").exists()  # cleaned up
    kinds = journal_kinds(run)
    assert kinds[0] == "run.started"
    assert "runtime.session" in kinds and "runtime.tool_call" in kinds
    assert kinds.count("step.started") == 7
    assert "verify.gate" in kinds

    with db.session() as session:
        card = store.get_card(session, card_id)
        verdicts = [c for c in card.comments if c.author == CommentAuthor.REVIEWER]
        assert len(verdicts) == 1 and "Verdict: met" in verdicts[0].body
        assert card.summary == run.summary
    assert reviewer.requests[0].diff_stats["files"][0]["path"] == "hello.py"
    assert reviewer.requests[0].gates[0]["passed"] is True

    merged = await engine.approve(card_id)
    assert card_state(db, card_id)[0] is CardStatus.DONE
    agent = Path(project.agent_path)
    assert (agent / "hello.py").exists()
    assert git(agent, "log", "-1", "--format=%s") == f"Merge card {card_id}: Add greet"
    assert merged.commit == git(agent, "rev-parse", "HEAD")
    # The person's own checkout is never touched.
    assert checkout_refs(project) == before
    assert not (Path(project.checkout_path) / "hello.py").exists()


async def test_cost_is_tracked(settings: Settings, db: Database, project: Project) -> None:
    card_id = add_card(db, project)
    script = [{"op": "cost", "usd": 0.25}, {"op": "cost", "usd": 0.5}, *WRITE_GREET]
    engine, _ = make_engine(settings, db, script)
    await engine.run_card(card_id)
    assert last_run(db, card_id).cost_usd == pytest.approx(0.75)


# Failure modes ---------------------------------------------------------------------


async def test_empty_diff_fails_even_when_agent_claims_success(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, [{"op": "finish", "message": "All done, great work!"}])
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    assert result.reason == "execute produced no changes"
    run = last_run(db, card_id)
    assert steps(run)["execute"] == "failed"
    assert steps(run)["verify"] == "skipped"
    assert steps(run)["finalize"] == "succeeded"


async def test_gate_failure_fails_the_run(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(
        project_toml=project_toml(test_code="import sys; print('2 failed'); sys.exit(1)")
    )
    card_id = add_card(db, project)
    reviewer = FakeReviewer()
    engine, _ = make_engine(settings, db, WRITE_GREET, reviewer=reviewer)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    assert result.reason and "gate 'test' failed (exit 1)" in result.reason
    run = last_run(db, card_id)
    verify = next(s for s in run.steps if s.name == StepName.VERIFY)
    assert "2 failed" in verify.detail["gates"][0]["output_tail"]
    assert steps(run)["critique"] == "skipped"
    assert reviewer.requests == []


async def test_blocked_run_moves_card_to_blocked_never_review(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    script = [
        {"op": "write", "path": "notes.md", "content": "partial\n"},
        {"op": "block", "reason": "Which database should the cache use?"},
    ]
    engine, _ = make_engine(settings, db, script)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.BLOCKED
    assert result.run_status is RunStatus.BLOCKED
    _status, reason = card_state(db, card_id)
    assert reason == "Which database should the cache use?"
    run = last_run(db, card_id)
    assert steps(run)["verify"] == "skipped"
    # Partial work is kept on the card branch for the next run.
    agent = Path(project.agent_path)
    assert "notes.md" in git(agent, "ls-tree", "-r", "--name-only", run.branch or "")

    with db.session() as session:
        store.set_card_status(session, store.get_card(session, card_id), CardStatus.READY)
    engine2, _ = make_engine(settings, db, WRITE_GREET)
    second = await engine2.run_card(card_id)
    assert second.card_status is CardStatus.REVIEW
    assert last_run(db, card_id).branch == run.branch


async def test_budget_exceeded_stops_the_agent(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project, budget_usd=1.0)
    script = [
        {"op": "write", "path": "first.txt", "content": "1"},
        {"op": "exceed_budget"},
        {"op": "write", "path": "after_budget.txt", "content": "should never exist"},
        {"op": "finish"},
    ]
    engine, runtime = make_engine(settings, db, script)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    assert result.reason and result.reason.startswith("budget exceeded")
    assert runtime.cancelled  # the engine told the runtime to stop
    assert ("write", "after_budget.txt") not in runtime.executed
    run = last_run(db, card_id)
    assert run.cost_usd > 1.0
    files = git(Path(project.agent_path), "ls-tree", "-r", "--name-only", run.branch or "")
    assert "first.txt" in files and "after_budget.txt" not in files


async def test_agent_error_fails_the_run(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, [{"op": "error", "message": "model overloaded"}])
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    assert result.reason == "agent error: model overloaded"


# Policy in the loop ---------------------------------------------------------------


async def test_protected_path_write_denied_and_push_blocked(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    script = [
        {"op": "write", "path": "migrations/001_drop.sql", "content": "DROP TABLE users;"},
        {"op": "write", "path": ".prat/project.toml", "content": 'name = "x"\nautonomy = "high"'},
        {"op": "bash", "command": "git push origin HEAD"},
        {"op": "bash", "command": "git -C . push"},
        {"op": "bash", "command": "sh -c 'git push'"},
        {"op": "bash", "command": f"git push {Path(project.checkout_path).as_posix()} HEAD"},
        *WRITE_GREET,
    ]
    engine, runtime = make_engine(settings, db, script)
    before = checkout_refs(project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW
    run = last_run(db, card_id)
    reasons = denials(run)
    assert len(reasons) == 6
    assert any("protected" in r for r in reasons)
    assert sum("push" in r for r in reasons) >= 3
    files = git(Path(project.agent_path), "ls-tree", "-r", "--name-only", run.branch or "")
    assert "migrations/001_drop.sql" not in files
    assert ("bash", "git push origin HEAD") not in runtime.executed
    assert checkout_refs(project) == before


async def test_effective_autonomy_is_the_lowest(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project, autonomy=Autonomy.READ_ONLY)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    result = await engine.run_card(card_id)
    run = last_run(db, card_id)
    assert run.autonomy is Autonomy.READ_ONLY
    assert result.card_status is CardStatus.FAILED  # the write was denied, so no diff
    assert any("read_only" in r for r in denials(run))


async def test_installation_ceiling_caps_the_project(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(project_toml=project_toml(autonomy="high"))
    card_id = add_card(db, project)
    engine, _ = make_engine(
        settings,
        db,
        [{"op": "bash", "command": "curl https://example.com"}, *WRITE_GREET],
        max_autonomy=Autonomy.LOW,
    )
    await engine.run_card(card_id)
    run = last_run(db, card_id)
    assert run.autonomy is Autonomy.LOW
    assert any("needs autonomy high" in r for r in denials(run))


class RogueRuntime:
    """Ignores the policy, to prove verification catches what the policy missed."""

    name = "rogue"

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    async def run(self, task: AgentTask) -> AsyncIterator[RuntimeEvent]:
        yield SessionStarted("rogue-1")
        for rel, content in self.files.items():
            target = task.workdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        yield DoneEvent(Outcome.COMPLETED, "done")

    def resume(self, session_id: str, task: AgentTask) -> AsyncIterator[RuntimeEvent]:
        return self.run(task)

    async def cancel(self, session_id: str) -> None:
        return None


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"migrations/1.sql": "x", "ok.py": "y"}, "protected"),
        ({"settings.py": "KEY = '" + "AKIA" + "R" * 16 + "'\n"}, "aws_access_key_id"),
        ({".env": "A=1\n"}, "secret file"),
        ({f"f{i}.txt": "x\n" for i in range(5)}, "touches 5 files"),
    ],
)
async def test_verification_catches_policy_bypass(
    settings: Settings,
    db: Database,
    project_factory: Callable[..., Project],
    files: dict[str, str],
    expected: str,
) -> None:
    project = project_factory(project_toml=project_toml(extra="max_diff_files = 4"))
    card_id = add_card(db, project)
    engine = Engine(settings, db, RogueRuntime(files), reviewer=FakeReviewer())
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    assert result.reason and expected in result.reason


# Cancel ------------------------------------------------------------------------------


async def test_cancel_mid_run_really_stops_the_runtime(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    script = [
        {"op": "write", "path": "started.txt", "content": "1"},
        {"op": "wait", "seconds": 60},
        {"op": "write", "path": "too_late.txt", "content": "2"},
        {"op": "finish"},
    ]
    engine, runtime = make_engine(settings, db, script)
    task = engine.submit(card_id)
    await wait_for(lambda: ("write", "started.txt") in runtime.executed)
    started = asyncio.get_running_loop().time()
    assert await engine.cancel(card_id)
    result = await asyncio.wait_for(task, timeout=20)
    assert asyncio.get_running_loop().time() - started < 10
    assert result.card_status is CardStatus.CANCELLED
    assert result.run_status is RunStatus.CANCELLED
    assert runtime.cancelled
    assert runtime.active == 0
    assert ("write", "too_late.txt") not in runtime.executed
    run = last_run(db, card_id)
    assert not Path(run.worktree_path or "").exists()


async def test_cancel_from_another_process(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    script = [{"op": "write", "path": "a.txt", "content": "1"}, {"op": "wait", "seconds": 60}]
    engine, runtime = make_engine(settings, db, script, stale_run_seconds=30)
    task = engine.submit(card_id)
    await wait_for(lambda: bool(runtime.executed))
    other, _ = make_engine(settings, db, [], stale_run_seconds=30)
    assert await other.cancel(card_id)  # writes a request; does not touch the run itself
    result = await asyncio.wait_for(task, timeout=20)
    assert result.card_status is CardStatus.CANCELLED


async def test_cancel_queued_card(settings: Settings, db: Database, project: Project) -> None:
    first = add_card(db, project, title="first")
    second = add_card(db, project, title="second")
    script = [{"op": "write", "path": "a.txt", "content": "1"}, {"op": "wait", "seconds": 60}]
    engine, runtime = make_engine(settings, db, script)
    t1 = engine.submit(first)
    t2 = engine.submit(second)
    await wait_for(lambda: bool(runtime.executed))
    assert await engine.cancel(second)
    queued = await asyncio.wait_for(t2, timeout=10)
    assert queued.run_id is None
    assert card_state(db, second)[0] is CardStatus.CANCELLED
    await engine.cancel(first)
    await asyncio.wait_for(t1, timeout=20)


async def test_cancel_idle_card(settings: Settings, db: Database, project: Project) -> None:
    card_id = add_card(db, project, ready=False)
    engine, _ = make_engine(settings, db, [])
    assert await engine.cancel(card_id)
    assert card_state(db, card_id)[0] is CardStatus.CANCELLED
    assert not await engine.cancel(card_id)


# Crash and resume ---------------------------------------------------------------------


async def test_crash_then_resume(settings: Settings, db: Database, project: Project) -> None:
    card_id = add_card(db, project)
    script = FakeScript.parse(
        {
            "steps": [
                {"op": "write", "path": "before_crash.txt", "content": "1\n"},
                {"op": "crash"},
                {"op": "write", "path": "after_crash.txt", "content": "2\n"},
                {"op": "finish"},
            ]
        }
    )
    engine, _ = make_engine(settings, db, script)
    with pytest.raises(FakeCrash):
        await engine.run_card(card_id)

    run = last_run(db, card_id)
    assert run.status is RunStatus.RUNNING  # nothing got to clean up: like a real crash
    assert steps(run)["prepare"] == "succeeded"
    assert steps(run)["execute"] == "running"
    assert card_state(db, card_id)[0] is CardStatus.RUNNING
    assert run.session_id

    # A new process starts: recover marks the run interrupted, then resume.
    restarted, runtime = make_engine(settings, db, script)
    assert restarted.recover() == [run.id]
    result = await restarted.resume(run.id)

    assert result.card_status is CardStatus.REVIEW
    assert runtime.sessions == [run.session_id]  # resumed by session id
    assert ("write", "before_crash.txt") not in runtime.executed
    resumed = last_run(db, card_id)
    assert resumed.id == run.id
    execute = next(s for s in resumed.steps if s.name == StepName.EXECUTE)
    prepare = next(s for s in resumed.steps if s.name == StepName.PREPARE)
    assert execute.attempts == 2
    assert prepare.attempts == 1  # finished steps are not repeated
    files = git(Path(project.agent_path), "ls-tree", "-r", "--name-only", resumed.branch or "")
    assert "before_crash.txt" in files and "after_crash.txt" in files
    assert "run.resumed" in journal_kinds(resumed)


async def test_resume_refuses_finished_runs(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    result = await engine.run_card(card_id)
    assert result.run_id is not None
    with pytest.raises(Exception, match="only interrupted runs resume"):
        await engine.resume(result.run_id)


async def test_recover_ignores_live_runs(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    script = [{"op": "write", "path": "a.txt", "content": "1"}, {"op": "wait", "seconds": 60}]
    engine, runtime = make_engine(settings, db, script, stale_run_seconds=30)
    task = engine.submit(card_id)
    await wait_for(lambda: bool(runtime.executed))
    other, _ = make_engine(settings, db, [], stale_run_seconds=30)
    assert other.recover() == []
    await engine.cancel(card_id)
    await asyncio.wait_for(task, timeout=20)


# Concurrency -------------------------------------------------------------------------


def _waiting_script(task: AgentTask) -> FakeScript:
    return FakeScript.parse(
        [
            {"op": "write", "path": f"card_{task.metadata['card_id']}.txt", "content": "x"},
            {"op": "wait", "seconds": 0.3},
            {"op": "finish"},
        ]
    )


async def test_per_project_concurrency_limit(
    settings: Settings, db: Database, project: Project
) -> None:
    cards = [add_card(db, project, title=f"card {i}") for i in range(3)]
    runtime = FakeRuntime(_waiting_script, state_dir=settings.home / "fake")
    engine, _ = make_engine(
        settings, db, runtime, max_concurrent_runs=3, max_concurrent_per_project=1
    )
    results = await asyncio.wait_for(
        asyncio.gather(*(engine.submit(c) for c in cards)), timeout=120
    )
    assert all(r.card_status is CardStatus.REVIEW for r in results)
    assert runtime.peak_active == 1
    assert engine.limiter.peak_by_project[project.id] == 1
    assert engine.limiter.peak == 1


async def test_global_concurrency_limit(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    projects = [project_factory(f"acme-{name}") for name in ("api", "web", "jobs")]
    cards = [add_card(db, p, title=f"card {i}") for p in projects for i in range(2)]
    runtime = FakeRuntime(_waiting_script, state_dir=settings.home / "fake")
    engine, _ = make_engine(
        settings, db, runtime, max_concurrent_runs=2, max_concurrent_per_project=2
    )
    results = await asyncio.wait_for(
        asyncio.gather(*(engine.submit(c) for c in cards)), timeout=180
    )
    assert all(r.card_status is CardStatus.REVIEW for r in results)
    # Slots are taken before any git work, so the limiter peak is exact; the
    # agents themselves may or may not overlap depending on timing.
    assert engine.limiter.peak == 2
    assert runtime.peak_active <= 2


async def test_submit_twice_is_rejected(settings: Settings, db: Database, project: Project) -> None:
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    task = engine.submit(card_id)
    with pytest.raises(Exception, match="already queued"):
        engine.submit(card_id)
    await task


# Review loop -------------------------------------------------------------------------


async def test_rework_passes_feedback_and_continues_the_branch(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    prompts: list[str] = []

    def script_for(task: AgentTask) -> FakeScript:
        prompts.append(task.prompt)
        n = len(prompts)
        return FakeScript.parse(
            [
                {"op": "write", "path": f"pass_{n}.txt", "content": f"{n}\n"},
                {"op": "finish"},
            ]
        )

    engine, _ = make_engine(settings, db, FakeRuntime(script_for))
    await engine.run_card(card_id)
    engine.rework(card_id, "Please also add a docstring.")
    assert card_state(db, card_id)[0] is CardStatus.REWORK
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW
    assert "Please also add a docstring." in prompts[1]
    assert "Please also add a docstring." not in prompts[0]
    with db.session() as session:
        card = session.get(Card, card_id)
        assert card is not None and len(card.runs) == 2
        assert card.runs[1].attempt == 2
        summary = card.summary or ""
    assert "pass_1.txt" in summary and "pass_2.txt" in summary


async def test_approve_requires_review(settings: Settings, db: Database, project: Project) -> None:
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, [])
    with pytest.raises(ApproveError, match="only cards in review"):
        await engine.approve(card_id)


async def test_merge_conflict_keeps_card_in_review(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(files={"app.py": "print('v1')\n"})
    card_id = add_card(db, project)
    script = [{"op": "write", "path": "app.py", "content": "print('card')\n"}, {"op": "finish"}]
    engine, _ = make_engine(settings, db, script)
    await engine.run_card(card_id)
    agent = Path(project.agent_path)
    (agent / "app.py").write_text("print('main moved on')\n", encoding="utf-8")
    git(agent, "commit", "-q", "-am", "concurrent change")
    with pytest.raises(ApproveError, match="conflicts"):
        await engine.approve(card_id)
    status, _ = card_state(db, card_id)
    assert status is CardStatus.REVIEW
    with db.session() as session:
        card = store.get_card(session, card_id)
        assert any(c.kind == "merge_conflict" for c in card.comments)


async def test_project_file_from_default_branch_wins(
    settings: Settings, db: Database, project: Project
) -> None:
    # The committed project file says autonomy "medium"; the database copy is
    # edited to "high". The run must follow what is committed.
    with db.session() as session:
        proj = store.get_project(session, project.id)
        proj.config = {**proj.config, "autonomy": "high"}
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    await engine.run_card(card_id)
    assert last_run(db, card_id).autonomy is Autonomy.MEDIUM


async def test_run_card_rejects_wrong_status(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project, ready=False)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    with pytest.raises(Exception, match="only ready or rework"):
        await engine.run_card(card_id)
    with db.session() as session:
        runs = session.query(Run).filter(Run.card_id == card_id).count()
    assert runs == 0


async def test_a_rerun_brings_the_current_project_file_onto_the_card_branch(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    seen: list[bool] = []

    def script_for(task: AgentTask) -> FakeScript:
        seen.append((Path(task.workdir) / ".prat" / "setup.sh").is_file())
        n = len(seen)
        return FakeScript.parse(
            [{"op": "write", "path": f"pass_{n}.txt", "content": f"{n}\n"}, {"op": "finish"}]
        )

    engine, _ = make_engine(settings, db, FakeRuntime(script_for))
    await engine.run_card(card_id)
    # The person adds a setup script to the project file after the card's branch was made.
    checkout = Path(project.checkout_path)
    (checkout / ".prat" / "setup.sh").write_text("echo setup\n", encoding="utf-8", newline="\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "add a setup script")
    main_head = git(checkout, "rev-parse", "HEAD").strip()

    engine.rework(card_id, "Please also add a docstring.")
    result = await engine.run_card(card_id)

    assert result.card_status is CardStatus.REVIEW
    assert seen == [False, True]
    with db.session() as session:
        card = session.get(Card, card_id)
        assert card is not None
        second = card.runs[1]
        assert second.base_commit == main_head  # the merge moved the base
        summary = card.summary or ""
    assert ".prat/setup.sh" not in summary and "pass_2.txt" in summary


async def test_a_rework_that_only_shows_something_still_reaches_review(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    runs = 0

    def script_for(task: AgentTask) -> FakeScript:
        nonlocal runs
        runs += 1
        if runs == 1:
            return FakeScript.parse(
                [{"op": "write", "path": "pass_1.txt", "content": "1\n"}, {"op": "finish"}]
            )
        # The second run only runs a check and reports it: no new commit.
        return FakeScript.parse(
            [{"op": "bash", "command": "ls"}, {"op": "finish", "message": "checked: ok"}]
        )

    engine, _ = make_engine(settings, db, FakeRuntime(script_for))
    await engine.run_card(card_id)
    engine.rework(card_id, "Show the health check output.")
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW
    # A first run that changes nothing still fails.
    other = add_card(db, project, title="Nothing")
    engine2, _ = make_engine(settings, db, [{"op": "finish"}])
    assert (await engine2.run_card(other)).card_status is CardStatus.FAILED


async def test_a_restart_interrupts_running_and_queued_cards_and_picks_them_up(
    settings: Settings, db: Database, project: Project
) -> None:
    running = add_card(db, project, title="Long one")
    waiting = add_card(db, project, title="Queued one")
    script = [{"op": "wait", "seconds": 30}, *WRITE_GREET]
    engine, _ = make_engine(settings, db, script, max_concurrent_per_project=1)
    first = engine.submit(running)
    engine.submit(waiting)
    for _ in range(100):
        if engine.is_active(running):
            break
        await asyncio.sleep(0.05)
    await engine.suspend()

    assert first.cancelled() or first.done()
    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, running))
        assert run is not None and run.status is RunStatus.INTERRUPTED
        assert store.get_card(session, waiting).status is CardStatus.READY
        assert store.get_card(session, waiting).status_reason == "PRAT restarted"

    # The next process picks both up: the run resumes, the queued card queues again.
    later, _ = make_engine(settings, db, [*WRITE_GREET])
    picked = later.resume_after_restart()
    assert set(picked) == {running, waiting}
    for card_id in (running, waiting):
        task = later._tasks[card_id]
        await task
    for card_id in (running, waiting):
        assert card_state(db, card_id)[0] is CardStatus.REVIEW
