"""Finish checks: PRAT's gates run on a builder's work as it ends its turn, and a failure is
handed back to it in the same session (settings.finish_checks)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import add_card, git, make_engine, project_toml
from prat import store
from prat.config import Settings
from prat.db import Database, Project, Run
from prat.domain import CardStatus
from prat.journal import read_journal
from prat.runtime import AgentTask, FakeRuntime, FakeScript

pytestmark = pytest.mark.integration

# Passes once ok.txt exists.
NEEDS_OK = "import pathlib, sys; sys.exit(0 if pathlib.Path('ok.txt').exists() else 1)"
GREET = {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"}
FIX = {"op": "write", "path": "ok.txt", "content": "ok\n"}


def events(db: Database, card_id: int) -> tuple[Run, list[Any]]:
    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None and run.journal_path
        return run, list(read_journal(Path(run.journal_path)))


def checks(journal: list[Any]) -> list[bool]:
    return [e.data["passed"] for e in journal if e.kind == "verify.finish_check"]


async def test_a_failed_gate_is_fixed_in_the_same_session(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(project_toml=project_toml(test_code=NEEDS_OK))
    script = [GREET, {"op": "finish"}, FIX, {"op": "finish", "message": "fixed"}]
    engine, runtime = make_engine(settings, db, script, race_builders=1)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    await engine.checkouts_set_up()
    assert result.card_status is CardStatus.REVIEW, result.reason
    run, journal = events(db, card_id)
    # One session, told once, and the pass at the second finish is the one verify keeps.
    assert len(runtime.sessions) == 1
    assert checks(journal) == [False, True]
    kinds = [e.kind for e in journal]
    assert "verify.speculative_kept" in kinds
    assert git(Path(project.agent_path), "show", f"{run.branch}:ok.txt") == "ok"


async def test_a_builder_with_nothing_more_to_do_fails_the_run_as_before(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(project_toml=project_toml(test_code=NEEDS_OK))
    engine, _ = make_engine(settings, db, [GREET, {"op": "finish"}], race_builders=1)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    assert "verification failed" in (result.reason or "")
    assert checks(events(db, card_id)[1]) == [False]


async def test_the_builder_is_told_at_most_finish_check_rounds_times(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(project_toml=project_toml(test_code=NEEDS_OK))
    never = [GREET, *([{"op": "finish"}, {"op": "text", "text": "trying"}] * 4), {"op": "finish"}]
    engine, _ = make_engine(settings, db, never, race_builders=1, finish_check_rounds=2)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    assert checks(events(db, card_id)[1]) == [False, False]


async def test_switched_off_the_first_finish_ends_the_session(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(project_toml=project_toml(test_code=NEEDS_OK))
    script = [GREET, {"op": "finish"}, FIX, {"op": "finish"}]
    engine, _ = make_engine(settings, db, script, race_builders=1, finish_checks=False)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    assert checks(events(db, card_id)[1]) == []


async def test_what_a_gate_writes_in_the_builders_worktree_is_undone(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    # The gate leaves a file behind and rewrites the builder's own.
    messy = (
        "import pathlib; pathlib.Path('junk.txt').write_text('x'); "
        "pathlib.Path('hello.py').write_text('changed')"
    )
    project = project_factory(project_toml=project_toml(test_code=messy))
    engine, _ = make_engine(settings, db, [GREET, {"op": "finish"}], race_builders=1)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    await engine.checkouts_set_up()
    assert result.card_status is CardStatus.REVIEW, result.reason
    run, journal = events(db, card_id)
    agent = Path(project.agent_path)
    assert "junk.txt" not in git(agent, "ls-tree", "-r", "--name-only", str(run.branch))
    assert git(agent, "show", f"{run.branch}:hello.py").startswith("def greet")
    restored = [e.data["paths"] for e in journal if e.kind == "verify.finish_restored"]
    assert restored and sorted(restored[0]) == ["hello.py", "junk.txt"]


async def test_a_rival_told_to_fix_its_work_does_not_win_until_it_passes(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(project_toml=project_toml(test_code=NEEDS_OK))
    # The rival finishes first with failing work and has no fix; the lead is slower and right.
    lead = [{"op": "wait", "seconds": 1.5}, GREET, FIX, {"op": "finish", "message": "lead"}]
    rival = [
        {"op": "write", "path": "rival.py", "content": "x = 1\n"},
        {"op": "finish"},
        {"op": "wait", "seconds": 30},
        {"op": "finish"},
    ]

    def script_for(task: AgentTask) -> FakeScript:
        return FakeScript.parse(rival if task.metadata.get("role") == "rival" else lead)

    engine, _ = make_engine(settings, db, FakeRuntime(script_for), race_builders=2)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    await engine.checkouts_set_up()
    assert result.card_status is CardStatus.REVIEW, result.reason
    run, _ = events(db, card_id)
    files = git(Path(project.agent_path), "ls-tree", "-r", "--name-only", str(run.branch))
    assert "hello.py" in files and "rival.py" not in files


async def test_a_loser_cut_off_mid_check_lets_go_of_its_worktree(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    # The gate takes two seconds. The lead reaches its finish first and is waiting on the gate
    # in its own worktree when the rival, whose gate result is already in, wins the race.
    slow = "import time; time.sleep(2)"
    project = project_factory(project_toml=project_toml(test_code=slow))
    lead = [{"op": "wait", "seconds": 1.5}, GREET, {"op": "finish", "message": "lead"}]
    rival = [
        {"op": "write", "path": "rival.py", "content": "x = 1\n"},
        {"op": "finish", "message": "rival"},
    ]

    def script_for(task: AgentTask) -> FakeScript:
        return FakeScript.parse(rival if task.metadata.get("role") == "rival" else lead)

    engine, _ = make_engine(settings, db, FakeRuntime(script_for), race_builders=2)
    card_id = add_card(db, project)
    result = await asyncio.wait_for(engine.run_card(card_id), timeout=60)
    await engine.checkouts_set_up()
    assert result.card_status is CardStatus.REVIEW, result.reason
    run, _ = events(db, card_id)
    files = git(Path(project.agent_path), "ls-tree", "-r", "--name-only", str(run.branch))
    assert "rival.py" in files and "hello.py" not in files
