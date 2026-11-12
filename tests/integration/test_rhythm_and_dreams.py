"""Rhythm (when a person works) and dreams (what PRAT does when they do not)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from conftest import add_card
from prat import dream, rhythm
from prat.config import Settings
from prat.db import Database, Project
from prat.db.models import ActivityBucket, Dream
from prat.rhythm import RhythmSettings

pytestmark = pytest.mark.integration

UTC_ONLY = RhythmSettings(mode="learn", timezone="UTC")


def work_days(db: Database, start: datetime, days: int, hours: range) -> None:
    for day in range(days):
        for hour in hours:
            moment = (start + timedelta(days=day)).replace(hour=hour, minute=10)
            rhythm.record_signal(db, moment, UTC)


def around_now(db: Database) -> None:
    """Fixed quiet hours that contain the real current time, so yielding can be tested."""
    hour = datetime.now(UTC).hour
    rhythm.save_settings(
        db, RhythmSettings("fixed", "UTC", fixed_start=(hour - 1) % 24, fixed_end=(hour + 2) % 24)
    )


# Rhythm -------------------------------------------------------------------------------------


def test_with_little_history_the_fallback_window_is_used(db: Database) -> None:
    rhythm.save_settings(db, UTC_ONLY)
    noon = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    window = rhythm.dream_window(db, noon)
    assert window is not None and not window.learned
    assert window.start == datetime(2026, 11, 12, 1, 0, tzinfo=UTC)
    assert window.end - window.start == timedelta(hours=4)


def test_the_dream_window_is_learned_from_working_hours(db: Database) -> None:
    rhythm.save_settings(db, UTC_ONLY)
    # Two weeks of work from 08:00 to 23:59 every day.
    work_days(db, datetime(2026, 10, 26, tzinfo=UTC), 15, range(8, 24))
    assert rhythm.active_days(db) >= rhythm.MIN_ACTIVE_DAYS
    noon = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    window = rhythm.dream_window(db, noon)
    assert window is not None and window.learned
    # Quiet from 00:00 to 08:00, trimmed by an hour at each end.
    assert window.start == datetime(2026, 11, 10, 1, 0, tzinfo=UTC)
    assert window.end == datetime(2026, 11, 10, 7, 0, tzinfo=UTC)
    heat = rhythm.heatmap(db, noon)
    assert len(heat) == 7 and all(len(row) == 24 for row in heat)
    assert heat[0][3] < rhythm.AWAKE_THRESHOLD <= heat[0][14]
    assert rhythm.state(db, noon).name == "awake"


def test_old_weeks_are_dropped(db: Database) -> None:
    rhythm.save_settings(db, UTC_ONLY)
    rhythm.record_signal(db, datetime(2026, 1, 5, 9, tzinfo=UTC), UTC)
    rhythm.record_signal(db, datetime(2026, 11, 9, 9, tzinfo=UTC), UTC)
    with db.session() as session:
        weeks = set(session.scalars(select(ActivityBucket.week_start)))
    assert weeks == {"2026-11-09"}


def test_a_signal_in_the_window_makes_prat_yield(db: Database) -> None:
    around_now(db)
    assert rhythm.state(db).name == "dreaming"
    rhythm.record_signal(db)
    current = rhythm.state(db)
    assert current.name == "awake" and current.reason == "someone is using PRAT"


def test_dreaming_can_be_turned_off(db: Database) -> None:
    rhythm.save_settings(db, RhythmSettings("never"))
    assert rhythm.dream_window(db) is None
    assert rhythm.state(db).name == "awake"
    with pytest.raises(ValueError, match="time zone"):
        rhythm.save_settings(db, RhythmSettings("learn", "Mars/Olympus"))


# Dreams -------------------------------------------------------------------------------------


def dream_row(db: Database, dream_id: int) -> Dream:
    with db.session() as session:
        row = session.get(Dream, dream_id)
        assert row is not None
        session.expunge(row)
        return row


async def test_a_dream_runs_every_phase(settings: Settings, db: Database, project: Project) -> None:
    result = await dream.run_dream(db, settings, None, trigger="manual", forced=True)
    assert result.status == "succeeded", result.phases
    assert list(result.phases) == list(dream.PHASES)
    remembered = result.phases["remember"]["projects"]
    assert [p["slug"] for p in remembered] == [project.slug]
    assert "health" in result.phases["reflect"]
    row = dream_row(db, result.dream_id)
    assert row.status == "succeeded" and row.finished_at is not None


async def test_a_dream_stops_when_someone_needs_the_machine(
    settings: Settings, db: Database, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def busy_after_tidy(_db: Database, *, forced: bool = False) -> bool:
        calls.append(1)
        return len(calls) > 1

    monkeypatch.setattr(dream, "should_yield", busy_after_tidy)
    result = await dream.run_dream(db, settings, None, forced=True)
    assert result.status == "interrupted"
    assert list(result.phases) == ["tidy"]
    assert dream_row(db, result.dream_id).results["interrupted_at"] == "remember"


async def test_a_person_card_running_holds_the_dream(
    settings: Settings, db: Database, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_card(db, project)
    monkeypatch.setattr(dream, "_person_cards_busy", lambda _db: True)
    assert dream.should_yield(db, forced=True)
    result = await dream.run_dream(db, settings, None, forced=True)
    assert result.status == "interrupted" and result.phases == {}


async def test_the_scheduler_dreams_once_per_window(
    settings: Settings, db: Database, project: Project
) -> None:
    around_now(db)
    scheduler = dream.DreamScheduler(db, settings, lambda: None, tick_seconds=3600)
    await scheduler.tick()
    assert scheduler.dreaming
    first: Any = scheduler._dream
    outcome = await first
    assert outcome.status == "succeeded", outcome.phases
    await scheduler.tick()
    assert not scheduler.dreaming  # this window has had its dream
    (only,) = dream.recent_dreams(db)
    assert only.trigger == "schedule" and only.window_start
    await scheduler.stop()


async def test_no_dream_outside_the_window(settings: Settings, db: Database) -> None:
    hour = datetime.now(UTC).hour
    rhythm.save_settings(
        db, RhythmSettings("fixed", "UTC", fixed_start=(hour + 3) % 24, fixed_end=(hour + 5) % 24)
    )
    scheduler = dream.DreamScheduler(db, settings, lambda: None)
    await scheduler.tick()
    assert not scheduler.dreaming and dream.recent_dreams(db) == []
