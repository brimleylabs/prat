"""The reviewer works while the gates run; its verdict counts only if they pass."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from conftest import WRITE_GREET, add_card, make_engine, project_toml
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus
from prat.engine import FakeReviewer
from prat.engine.interfaces import ReviewRequest, ReviewResult
from prat.journal import read_journal
from prat.runtime import FakeRuntime

pytestmark = pytest.mark.integration


class SlowReviewer(FakeReviewer):
    """Records when each review starts, and takes a moment."""

    def __init__(self) -> None:
        super().__init__()
        self.started: list[float] = []

    async def review(self, request: ReviewRequest) -> ReviewResult:
        self.started.append(asyncio.get_running_loop().time())
        await asyncio.sleep(0.5)
        return await super().review(request)


def journal_kinds(db: Database, card_id: int) -> list[str]:
    from pathlib import Path

    from prat import store

    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None and run.journal_path
        return [e.kind for e in read_journal(Path(run.journal_path))]


async def test_one_review_runs_alongside_the_gates_and_is_kept(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    # A gate that takes a second: the review overlaps it.
    project = project_factory(project_toml=project_toml(test_code="import time; time.sleep(1)"))
    reviewer = SlowReviewer()
    engine, _ = make_engine(settings, db, WRITE_GREET, reviewer=reviewer)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW
    assert len(reviewer.requests) == 1
    assert "critique.early" in journal_kinds(db, card_id)
    gates = reviewer.requests[0].gates
    assert gates and gates[0].get("pending")


async def test_a_failing_gate_discards_the_early_review(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(project_toml=project_toml(test_code="import sys; sys.exit(3)"))
    reviewer = SlowReviewer()
    engine, _ = make_engine(settings, db, WRITE_GREET, reviewer=reviewer)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.FAILED
    # No verdict was recorded for a change whose gates failed.
    from prat import store

    with db.session() as session:
        card = store.get_card(session, card_id)
        assert not [c for c in card.comments if c.kind == "verdict"]


async def test_without_it_the_review_waits_for_the_gates(
    settings: Settings, db: Database, project: Project
) -> None:
    reviewer = SlowReviewer()
    engine, _ = make_engine(settings, db, WRITE_GREET, reviewer=reviewer, review_during_gates=False)
    card_id = add_card(db, project)
    await engine.run_card(card_id)
    assert "critique.early" not in journal_kinds(db, card_id)
    assert not reviewer.requests[0].gates[0].get("pending")


def kinds_of(db: Database, card_id: int) -> list[str]:
    return journal_kinds(db, card_id)


async def test_the_reviewer_starts_when_the_builder_runs_the_tests(
    settings: Settings, db: Database, project: Project
) -> None:
    script = [
        {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"},
        {"op": "bash", "command": "echo pytest"},
        {"op": "wait", "seconds": 1},
        {"op": "finish"},
    ]
    reviewer = SlowReviewer()
    engine, _ = make_engine(settings, db, script, reviewer=reviewer)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW
    kinds = kinds_of(db, card_id)
    assert "critique.speculative" in kinds and "critique.speculative_kept" in kinds, [
        k for k in kinds if "critique" in k or "tool" in k
    ]
    assert len(reviewer.requests) == 1
    assert "hello.py" in reviewer.requests[0].diff


async def test_a_change_after_the_snapshot_is_reviewed_again(
    settings: Settings, db: Database, project: Project
) -> None:
    script = [
        {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"},
        {"op": "bash", "command": "echo pytest"},
        {"op": "wait", "seconds": 0.3},
        {"op": "write", "path": "later.py", "content": "x = 1\n"},
        {"op": "finish"},
    ]
    reviewer = SlowReviewer()
    engine, _ = make_engine(settings, db, script, reviewer=reviewer)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW
    assert "critique.speculative_kept" not in kinds_of(db, card_id)
    # The verdict that counts was given on the code that was handed over.
    assert "later.py" in reviewer.requests[-1].diff


async def test_the_winning_rivals_own_speculative_review_is_kept(
    settings: Settings, db: Database, project: Project
) -> None:
    from prat.runtime import AgentTask, FakeScript

    def script_for(task: AgentTask) -> FakeScript:
        if task.metadata.get("role") == "rival":
            return FakeScript.parse(
                [
                    {"op": "write", "path": "rival.py", "content": "x = 1\n"},
                    {"op": "bash", "command": "echo pytest"},
                    {"op": "wait", "seconds": 1},
                    {"op": "finish"},
                ]
            )
        return FakeScript.parse(
            [
                {"op": "write", "path": "lead.py", "content": "y = 1\n"},
                {"op": "wait", "seconds": 0.5},
                {"op": "bash", "command": "echo pytest"},
                {"op": "wait", "seconds": 20},
                {"op": "finish"},
            ]
        )

    reviewer = SlowReviewer()
    engine, _ = make_engine(
        settings, db, FakeRuntime(script_for), reviewer=reviewer, race_builders=2
    )
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    await engine.checkouts_set_up()
    assert result.card_status is CardStatus.REVIEW
    assert "critique.speculative_kept" in journal_kinds(db, card_id)
    kept = [r for r in reviewer.requests if "rival.py" in r.diff]
    assert kept and "lead.py" not in kept[0].diff


async def test_the_gates_start_on_the_snapshot_and_their_result_is_kept(
    settings: Settings, db: Database, project: Project
) -> None:
    script = [
        {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"},
        {"op": "bash", "command": "echo pytest"},
        {"op": "wait", "seconds": 1},
        {"op": "finish"},
    ]
    engine, _ = make_engine(settings, db, script, reviewer=SlowReviewer())
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    await engine.checkouts_set_up()
    assert result.card_status is CardStatus.REVIEW
    kinds = kinds_of(db, card_id)
    assert "verify.speculative_started" in kinds and "verify.speculative_kept" in kinds
    assert "verify.gate" in kinds


async def test_gates_on_an_old_snapshot_are_not_kept(
    settings: Settings, db: Database, project: Project
) -> None:
    script = [
        {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"},
        {"op": "bash", "command": "echo pytest"},
        {"op": "wait", "seconds": 0.3},
        {"op": "write", "path": "later.py", "content": "x = 1\n"},
        {"op": "finish"},
    ]
    engine, _ = make_engine(settings, db, script, reviewer=SlowReviewer())
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    await engine.checkouts_set_up()
    assert result.card_status is CardStatus.REVIEW
    kinds = kinds_of(db, card_id)
    assert "verify.speculative_started" in kinds
    assert "verify.speculative_kept" not in kinds
