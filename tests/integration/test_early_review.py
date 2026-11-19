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
