"""Mission control: the snapshot of everything PRAT is doing, and the terminal app on it."""

from __future__ import annotations

import pytest

from conftest import add_card, make_engine
from prat import store
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus
from prat.mission import MissionData, route_of, sparkline
from prat.mission_app import MissionControl

pytestmark = pytest.mark.integration

SCRIPT = [
    {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"},
    {"op": "bash", "command": "git push origin HEAD"},
    {"op": "finish", "message": "greet added"},
]


async def in_review(settings: Settings, db: Database, project: Project) -> int:
    engine, _ = make_engine(settings, db, SCRIPT)
    card_id = add_card(db, project, title="Add greet")
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW, result.reason
    return card_id


async def test_the_snapshot_shows_what_needs_you_and_what_the_policy_refused(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = await in_review(settings, db, project)
    snap = MissionData(settings, db).snapshot()
    (waiting,) = snap.waiting
    assert waiting.card_id == card_id and waiting.status == "review"
    assert waiting.gates_total >= 1 and waiting.gates_passed == waiting.gates_total
    assert not snap.flights
    refusal = next(r for r in snap.refusals if r.card_id == card_id)
    assert "git push" in refusal.title
    assert [p.slug for p in snap.projects] == [project.slug]
    assert snap.pulse.runs_today == 1


async def test_a_card_is_approved_from_the_terminal(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = await in_review(settings, db, project)
    app = MissionControl(settings, db)
    async with app.run_test(size=(160, 44)) as pilot:
        for _ in range(50):
            await pilot.pause(0.1)
            if app.snapshot is not None:
                break
        assert app.selected == card_id
        await pilot.press("a")
        await pilot.pause(0.2)
        await pilot.press("y")
        for _ in range(100):
            await pilot.pause(0.1)
            with db.session() as session:
                if store.get_card(session, card_id).status is CardStatus.DONE:
                    break
    with db.session() as session:
        assert store.get_card(session, card_id).status is CardStatus.DONE
    landed = MissionData(settings, db).snapshot().landed
    assert [item.card_id for item in landed] == [card_id] and landed[0].commit


def test_a_card_not_yet_run_has_every_leg_ahead() -> None:
    from datetime import UTC, datetime

    route = route_of(None, datetime.now(UTC))
    assert [leg.name for leg in route] == [
        "prepare", "plan", "build", "verify", "review", "summary", "finish",
    ]  # fmt: skip
    assert {leg.state for leg in route} == {"pending"}


def test_sparkline_scales_to_the_busiest_hour() -> None:
    assert sparkline([0.0, 1.0, 2.0]) == "▁▄█"
    assert sparkline([0.0, 0.0]) == "▁▁"


async def test_every_screen_draws_from_a_real_installation(
    settings: Settings, db: Database, project: Project
) -> None:
    await in_review(settings, db, project)
    app = MissionControl(settings, db)
    async with app.run_test(size=(170, 46)) as pilot:
        for _ in range(50):
            await pilot.pause(0.1)
            if app.snapshot is not None:
                break
        assert app.snapshot is not None
        assert app.snapshot.crew, "the run's agents should remember it"
        for key, view in zip(
            "2345671", ("crew", "jobs", "epics", "machine", "skills", "usage", "deck"), strict=True
        ):
            await pilot.press(key)
            await pilot.pause(0.2)
            assert app.view == view
        assert app.snapshot.usage == [] or app.snapshot.usage[0].model
