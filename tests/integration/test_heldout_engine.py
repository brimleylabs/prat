"""Held-out tests in the engine: written from the card while the builder works, run on the
work when its own checks pass, and handed back once when they fail (prat.engine.heldout)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from conftest import add_card, git, make_engine
from prat import store
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus
from prat.engine import heldout
from prat.journal import read_journal
from prat.runtime import AgentTask, FakeRuntime, FakeScript

pytestmark = pytest.mark.integration

GREET = {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"}
FIX = {"op": "write", "path": "ok.txt", "content": "ok\n"}
TEST = {
    "path": "card_check.py",
    "content": "import pathlib, sys; sys.exit(0 if pathlib.Path('ok.txt').exists() else 1)\n",
    "command": f'"{sys.executable}" card_check.py',
}


def crew(seen: list[AgentTask]) -> FakeRuntime:
    def script_for(task: AgentTask) -> FakeScript:
        seen.append(task)
        if task.metadata.get("role") == "researcher":
            answer = "```json\n" + json.dumps(TEST) + "\n```"
            return FakeScript.parse([{"op": "finish", "message": answer}])
        return FakeScript.parse([GREET, {"op": "finish"}, FIX, {"op": "finish", "message": "ok"}])

    return FakeRuntime(script_for)


def journal_of(db: Database, card_id: int) -> tuple[Any, list[Any]]:
    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None and run.journal_path
        return run, list(read_journal(Path(run.journal_path)))


async def test_a_held_out_test_that_fails_goes_back_once_and_never_joins_the_work(
    settings: Settings, db: Database, project: Project
) -> None:
    seen: list[AgentTask] = []
    engine, _ = make_engine(settings, db, crew(seen), race_builders=1, held_out_tests="all")
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    await engine.checkouts_set_up()
    assert result.card_status is CardStatus.REVIEW, result.reason
    run, journal = journal_of(db, card_id)
    ran = [e.data for e in journal if e.kind == "verify.held_out"]
    assert [(r["round"], r["failed"]) for r in ran] == [(1, True), (2, False)]
    # The writer saw the code as it was, never the builder's worktree.
    writer = next(t for t in seen if t.metadata.get("role") == "researcher")
    assert Path(writer.workdir) == Path(project.agent_path)
    files = git(Path(project.agent_path), "ls-tree", "--name-only", "-r", run.branch).split()
    assert "ok.txt" in files and "card_check.py" not in files
    note = heldout.read_note(settings.run_dir(run.id))
    assert "after the builder was shown it failing" in note
    assert "A test written from the card alone" in engine.reviewer.requests[-1].knowledge
    saved = heldout.load(settings.home, card_id)
    assert saved is not None and saved.usable


async def test_no_held_out_test_is_written_unless_asked_for(
    settings: Settings, db: Database, project: Project
) -> None:
    seen: list[AgentTask] = []
    engine, _ = make_engine(settings, db, crew(seen), race_builders=1, held_out_tests="high")
    card_id = add_card(db, project)  # not a high-complexity card
    await engine.run_card(card_id)
    assert not [t for t in seen if t.metadata.get("role") == "researcher"]
    assert heldout.load(settings.home, card_id) is None
