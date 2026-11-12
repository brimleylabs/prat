"""The usage page: where model usage went, by kind of work, outcome, project and card."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update

from conftest import add_card
from prat.db import Card, Database, ImproveCycle, Project, Run, RunStep
from prat.domain import CardStatus, CycleStatus, CycleTrigger, RunStatus, StepName, StepStatus
from prat.web.data import load_usage

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)


def add_run(
    db: Database,
    card_id: int,
    status: RunStatus,
    total: float,
    steps: dict[StepName, dict[str, Any]],
    *,
    when: datetime = NOW,
) -> None:
    with db.session() as session:
        run = Run(
            card_id=card_id,
            status=RunStatus.QUEUED,
            runtime="fake",
            cost_usd=total,
            created_at=when,
        )
        run.status = RunStatus.RUNNING
        run.status = status
        session.add(run)
        session.flush()
        for position, (name, detail) in enumerate(steps.items()):
            step = RunStep(
                run_id=run.id,
                name=name,
                position=position,
                status=StepStatus.PENDING,
                detail=detail,
            )
            step.status = StepStatus.RUNNING
            step.status = StepStatus.SUCCEEDED
            session.add(step)


def set_status(db: Database, card_id: int, status: CardStatus) -> None:
    with db.session() as session:
        session.execute(update(Card).where(Card.id == card_id).values(status=status))


def test_usage_breaks_spend_down(db: Database, project: Project) -> None:
    landed = add_card(db, project, "landed")
    failed = add_card(db, project, "failed")
    add_run(
        db,
        landed,
        RunStatus.SUCCEEDED,
        1.0,
        {
            StepName.PLAN: {"cost_usd": 0.1},
            StepName.EXECUTE: {"cost_usd": 0.6},
            StepName.CRITIQUE: {"cost_usd": 0.2, "security": {"cost_usd": 0.05}},
        },
    )
    add_run(db, failed, RunStatus.FAILED, 0.5, {StepName.EXECUTE: {"cost_usd": 0.5}})
    add_run(db, failed, RunStatus.FAILED, 9.0, {}, when=NOW - timedelta(days=60))
    set_status(db, landed, CardStatus.DONE)
    with db.session() as session:
        session.add(
            ImproveCycle(
                trigger=CycleTrigger.SCHEDULE,
                status=CycleStatus.SUCCEEDED,
                started_at=NOW - timedelta(days=1),
                cost_usd=0.25,
            )
        )

    with db.session() as session:
        page = load_usage(session, 30, NOW)

    assert page.runs == 2
    assert page.total == pytest.approx(1.75)
    roles = {r.key: round(r.usd, 4) for r in page.roles}
    assert roles == {
        "planner": 0.1,
        "builder": 1.1,
        "reviewer": 0.2,
        "security": 0.05,
        "improve": 0.25,
        "other": 0.05,
    }
    outcomes = {o.key: o.usd for o in page.outcomes}
    assert outcomes == {"landed": 1.0, "open": 0.0, "unfinished": 0.5}
    assert page.landed_cards == 1 and page.per_landed == 1.0
    assert [c.label for c in page.cards] == [f"#{landed} landed", f"#{failed} failed"]
    assert page.cards[0].width == 100.0
    assert len(page.daily) == 30 and page.daily[-1].usd == pytest.approx(1.5)
    assert page.daily[-2].usd == 0.25


def test_usage_with_nothing_run(db: Database) -> None:
    with db.session() as session:
        page = load_usage(session, 7, NOW)
    assert page.total == 0 and page.runs == 0 and page.per_landed is None
    assert page.roles == [] and page.cards == []
