from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from conftest import add_card
from prat.db import Database, Project
from prat.db.models import AppSetting, ImproveEvent
from prat.improve import (
    EventKind,
    ImproveMode,
    ImproveSettings,
    load_improve_settings,
    recent_events,
    record_event,
    update_improve_settings,
)
from prat.improve.events import MAX_MESSAGE_CHARS
from prat.improve.settings import PREFIX
from prat.policy.secrets import REDACTED


def stored(db: Database) -> dict[str, Any]:
    with db.session() as session:
        return {row.key: row.value for row in session.scalars(select(AppSetting))}


def events(db: Database) -> list[tuple[str, dict[str, Any]]]:
    with db.session() as session:
        rows = session.scalars(select(ImproveEvent).order_by(ImproveEvent.id))
        return [(row.kind, dict(row.data)) for row in rows]


def put(db: Database, name: str, value: Any) -> None:
    with db.session() as session:
        session.add(AppSetting(key=PREFIX + name, value=value))


# Settings -------------------------------------------------------------------------


def test_defaults_are_off_and_bounded(db: Database) -> None:
    with db.session() as session:
        current = load_improve_settings(session)
    assert current == ImproveSettings()
    assert current.enabled is False
    assert current.mode is ImproveMode.AUTO
    assert (current.max_reworks, current.retry_cooldown_days) == (2, 30)
    assert (current.interval_minutes, current.window_days) == (360, 7)
    assert (current.daily_budget_usd, current.card_budget_usd) == (5.0, 2.0)
    assert (current.max_open_cards, current.max_new_cards_per_cycle) == (3, 2)
    assert current.research_enabled is True
    assert current.push_enabled is True
    assert stored(db) == {}


def test_update_stores_only_what_changed_and_records_who(db: Database) -> None:
    with db.session() as session:
        after = update_improve_settings(
            session, actor="cli", enabled=True, mode="review", max_open_cards=3, window_days=14
        )
    assert after.enabled is True
    assert after.mode is ImproveMode.REVIEW
    assert after.window_days == 14
    # max_open_cards was already 3: no row, no event.
    assert stored(db) == {
        "improve.enabled": True,
        "improve.mode": "review",
        "improve.window_days": 14,
    }
    assert events(db) == [
        ("toggled", {"enabled": True, "actor": "cli"}),
        ("mode_changed", {"from": "auto", "to": "review", "actor": "cli"}),
        (
            "settings_changed",
            {"from": {"window_days": 7}, "to": {"window_days": 14}, "actor": "cli"},
        ),
    ]
    with db.session() as session:
        assert load_improve_settings(session) == after


def test_setting_the_same_value_again_is_silent(db: Database) -> None:
    with db.session() as session:
        update_improve_settings(session, enabled=True)
    with db.session() as session:
        update_improve_settings(session, enabled=True)
    assert [kind for kind, _ in events(db)] == ["toggled"]


def test_turning_off_is_recorded(db: Database) -> None:
    with db.session() as session:
        update_improve_settings(session, actor="web", enabled=True)
    with db.session() as session:
        after = update_improve_settings(session, actor="web", enabled=False)
    assert after.enabled is False
    assert stored(db) == {"improve.enabled": False}
    assert events(db)[-1] == ("toggled", {"enabled": False, "actor": "web"})


def test_unknown_setting_is_refused(db: Database) -> None:
    with db.session() as session, pytest.raises(ValueError, match=r"unknown.*autopilot"):
        update_improve_settings(session, autopilot=True)
    assert stored(db) == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"interval_minutes": 1},
        {"daily_budget_usd": -1},
        {"card_budget_usd": 0},
        {"max_open_cards": 0},
        {"max_reworks": 99},
        {"mode": "yolo"},
        {"window_days": "soon"},
    ],
)
def test_invalid_value_is_refused_and_nothing_is_stored(
    db: Database, changes: dict[str, Any]
) -> None:
    with db.session() as session:
        with pytest.raises(ValueError, match="invalid self-improvement setting"):
            update_improve_settings(session, enabled=True, **changes)
        assert load_improve_settings(session).enabled is False
    assert stored(db) == {}
    assert events(db) == []


def test_a_bad_stored_row_falls_back_to_the_default_for_that_key_only(db: Database) -> None:
    put(db, "daily_budget_usd", 1_000_000_000)
    put(db, "mode", ["auto"])
    put(db, "enabled", {"yes": 1})
    put(db, "max_open_cards", 5)
    put(db, "no_such_setting", True)
    with db.session() as session:
        current = load_improve_settings(session)
    assert current.daily_budget_usd == 5.0
    assert current.mode is ImproveMode.AUTO
    assert current.enabled is False
    assert current.max_open_cards == 5


def test_keys_of_other_features_are_left_alone(db: Database) -> None:
    with db.session() as session:
        session.add(AppSetting(key="rhythm.enabled", value=True))
    with db.session() as session:
        update_improve_settings(session, push_enabled=False)
        assert load_improve_settings(session).enabled is False
    assert stored(db) == {"rhythm.enabled": True, "improve.push_enabled": False}


# The activity feed ----------------------------------------------------------------


def test_events_are_redacted_and_capped(db: Database, project: Project) -> None:
    card_id = add_card(db, project)
    with db.session() as session:
        record_event(
            session,
            EventKind.PUSH_FAILED,
            "push to https://bot:hunter2pass@example.invalid/prat.git refused " + "x" * 5000,
            card_id=card_id,
            data={"api_key": "abcd1234efgh", "attempt": 2},
        )
    with db.session() as session:
        event = recent_events(session)[0]
        assert event.kind == "push_failed"
        assert event.card_id == card_id
        assert "hunter2pass" not in event.message
        assert len(event.message) == MAX_MESSAGE_CHARS
        assert event.data == {"api_key": REDACTED, "attempt": 2}


def test_recent_events_are_newest_first_and_filterable(db: Database, project: Project) -> None:
    first, second = add_card(db, project, "first"), add_card(db, project, "second")
    with db.session() as session:
        record_event(session, EventKind.CYCLE_STARTED, "one")
        record_event(session, EventKind.CARD_FILED, "two", card_id=first)
        record_event(session, EventKind.CARD_LANDED, "three", card_id=first)
        record_event(session, EventKind.CARD_FILED, "four", card_id=second)
    with db.session() as session:
        assert [e.message for e in recent_events(session)] == ["four", "three", "two", "one"]
        assert [e.message for e in recent_events(session, limit=2)] == ["four", "three"]
        assert [e.message for e in recent_events(session, card_id=first)] == ["three", "two"]
        filed = recent_events(session, kinds=(EventKind.CARD_FILED,))
        assert [e.message for e in filed] == ["four", "two"]


def test_an_unknown_kind_is_refused(db: Database) -> None:
    with db.session() as session, pytest.raises(ValueError, match="made_up"):
        record_event(session, "made_up")  # type: ignore[arg-type]
