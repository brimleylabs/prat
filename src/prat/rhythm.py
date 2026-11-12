"""Rhythm: PRAT learns when its person works, so background work happens when they do not.

A signal is recorded whenever a person does something on purpose (a page view or an action in
the web UI, a command in a terminal). Only counts per local hour of the week are kept, for 12
weeks. From them PRAT scores each hour of the week, weighting recent weeks more (half-life three
weeks); hours scoring at or above the threshold are awake hours. The dream window of a night is
the longest stretch of quiet hours that starts in the evening or night, trimmed by an hour at each
end. With too little history PRAT uses a fallback window (01:00 to 05:00 local time).

A person can choose fixed quiet hours or no dreaming at all, and any signal during a dream makes
it yield.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select

from prat.db import Database, as_utc
from prat.db.models import ActivityBucket, AppSetting

HOURS = 7 * 24
KEEP_WEEKS = 12
HALF_LIFE_WEEKS = 3.0
AWAKE_THRESHOLD = 0.34
MIN_ACTIVE_DAYS = 5
FALLBACK = (1, 5)
MIN_WINDOW_HOURS = 2
YIELD_MINUTES = 15
SETTINGS_KEY = "rhythm"


@dataclass(frozen=True)
class RhythmSettings:
    mode: str = "learn"  # learn, fixed, never
    timezone: str = ""  # an IANA name; empty is the system's zone
    fixed_start: int = 1
    fixed_end: int = 5

    def zone(self) -> tzinfo:
        if self.timezone:
            with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
                return ZoneInfo(self.timezone)
        local = datetime.now().astimezone().tzinfo
        return local if local is not None else ZoneInfo("UTC")


def load_settings(db: Database) -> RhythmSettings:
    with db.session() as session:
        row = session.get(AppSetting, SETTINGS_KEY)
        raw: dict[str, Any] = (
            dict(row.value) if row is not None and isinstance(row.value, dict) else {}
        )
    mode = raw.get("mode") if raw.get("mode") in ("learn", "fixed", "never") else "learn"
    start, end = raw.get("fixed_start", 1), raw.get("fixed_end", 5)
    return RhythmSettings(
        mode=str(mode),
        timezone=str(raw.get("timezone") or ""),
        fixed_start=int(start) % 24 if isinstance(start, int) else 1,
        fixed_end=int(end) % 24 if isinstance(end, int) else 5,
    )


def save_settings(db: Database, settings: RhythmSettings) -> None:
    if settings.mode not in ("learn", "fixed", "never"):
        raise ValueError("mode is learn, fixed or never")
    if settings.timezone:
        try:
            ZoneInfo(settings.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {settings.timezone!r}") from exc
    value = {
        "mode": settings.mode,
        "timezone": settings.timezone,
        "fixed_start": settings.fixed_start % 24,
        "fixed_end": settings.fixed_end % 24,
    }
    with db.session() as session:
        row = session.get(AppSetting, SETTINGS_KEY)
        if row is None:
            session.add(AppSetting(key=SETTINGS_KEY, value=value))
        else:
            row.value = value


def _week_start(local: datetime) -> date:
    return (local - timedelta(days=local.weekday())).date()


def hour_of_week(local: datetime) -> int:
    return local.weekday() * 24 + local.hour


def record_signal(db: Database, when: datetime | None = None, zone: tzinfo | None = None) -> None:
    """Count one human action in its local hour of the week."""
    local = (when or datetime.now().astimezone()).astimezone(zone or load_settings(db).zone())
    week, hour = _week_start(local).isoformat(), hour_of_week(local)
    with db.session() as session:
        row = session.scalars(
            select(ActivityBucket).where(
                ActivityBucket.week_start == week, ActivityBucket.hour_of_week == hour
            )
        ).first()
        if row is None:
            session.add(
                ActivityBucket(
                    week_start=week, hour_of_week=hour, count=1, last_seen=datetime.now(UTC)
                )
            )
        else:
            row.count += 1
            row.last_seen = datetime.now(UTC)
        cutoff = (_week_start(local) - timedelta(weeks=KEEP_WEEKS)).isoformat()
        session.execute(delete(ActivityBucket).where(ActivityBucket.week_start < cutoff))


def scores(db: Database, now: datetime | None = None, zone: tzinfo | None = None) -> list[float]:
    """An activity score for each of the 168 hours of the week."""
    local = (now or datetime.now().astimezone()).astimezone(zone or load_settings(db).zone())
    this_week = _week_start(local)
    with db.session() as session:
        rows = session.execute(
            select(ActivityBucket.week_start, ActivityBucket.hour_of_week, ActivityBucket.count)
        ).all()
    if not rows:
        return [0.0] * HOURS
    weeks = sorted({date.fromisoformat(str(r[0])) for r in rows})
    first = weeks[0]
    span = [first + timedelta(weeks=i) for i in range(((this_week - first).days // 7) + 1)]
    span = span[-KEEP_WEEKS:]
    weight = {w: 0.5 ** (((this_week - w).days / 7) / HALF_LIFE_WEEKS) for w in span}
    total = sum(weight.values()) or 1.0
    active: dict[int, float] = {}
    for week_text, hour, count in rows:
        week = date.fromisoformat(str(week_text))
        if week in weight and count > 0:
            active[int(hour)] = active.get(int(hour), 0.0) + weight[week]
    return [round(active.get(h, 0.0) / total, 4) for h in range(HOURS)]


def active_days(db: Database) -> int:
    with db.session() as session:
        rows = session.execute(
            select(ActivityBucket.week_start, ActivityBucket.hour_of_week).where(
                ActivityBucket.count > 0
            )
        ).all()
    return len({(str(w), int(h) // 24) for w, h in rows})


def _window_from_quiet(quiet: Sequence[bool], day_start: int) -> tuple[int, int] | None:
    """The longest quiet run (in hours of the week, wrapping) that starts between 18:00 on the
    given day and 06:00 the next, trimmed by an hour each end."""
    best: tuple[int, int] | None = None
    for offset in range(18, 31):
        start = (day_start + offset) % HOURS
        if not quiet[start] or quiet[(start - 1) % HOURS]:
            continue
        length = 0
        while length < 24 and quiet[(start + length) % HOURS]:
            length += 1
        trimmed = length - 2
        if trimmed >= MIN_WINDOW_HOURS and (best is None or trimmed > best[1]):
            best = ((start + 1) % HOURS, trimmed)
    return best


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    learned: bool

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


def dream_window(db: Database, now: datetime | None = None) -> Window | None:
    """The current or next dream window, or ``None`` when dreaming is off."""
    conf = load_settings(db)
    if conf.mode == "never":
        return None
    zone = conf.zone()
    local = (now or datetime.now().astimezone()).astimezone(zone)
    candidates: list[Window] = []
    for day in (-1, 0, 1):
        anchor = (local + timedelta(days=day)).replace(hour=0, minute=0, second=0, microsecond=0)
        if conf.mode == "fixed" or active_days(db) < MIN_ACTIVE_DAYS:
            begin, finish = (conf.fixed_start, conf.fixed_end) if conf.mode == "fixed" else FALLBACK
            length = (finish - begin) % 24 or 24
            start = anchor + timedelta(hours=begin)
            candidates.append(Window(start, start + timedelta(hours=length), False))
            continue
        quiet = [score < AWAKE_THRESHOLD for score in scores(db, local, zone)]
        base = anchor.weekday() * 24
        found = _window_from_quiet(quiet, base)
        if found is not None:
            start = anchor + timedelta(hours=(found[0] - base) % HOURS)
            candidates.append(Window(start, start + timedelta(hours=found[1]), True))
    current = [w for w in candidates if w.contains(local)]
    if current:
        return current[0]
    upcoming = sorted((w for w in candidates if w.start > local), key=lambda w: w.start)
    return upcoming[0] if upcoming else None


def last_signal(db: Database) -> datetime | None:
    with db.session() as session:
        value = session.scalar(select(func.max(ActivityBucket.last_seen)))
    return as_utc(value)


@dataclass(frozen=True)
class State:
    name: str  # awake, dreaming, idle
    reason: str
    window: Window | None


def state(db: Database, now: datetime | None = None) -> State:
    """Whether PRAT should be dreaming now, and why."""
    conf = load_settings(db)
    local = (now or datetime.now().astimezone()).astimezone(conf.zone())
    if conf.mode == "never":
        return State("awake", "dreaming is turned off", None)
    window = dream_window(db, local)
    seen = last_signal(db)
    recent = seen is not None and local - seen < timedelta(minutes=YIELD_MINUTES)
    if window is not None and window.contains(local):
        if recent:
            return State("awake", "someone is using PRAT", window)
        return State("dreaming", "inside the dream window", window)
    if window is None:
        return State("idle", "no dream window could be found", None)
    hour = hour_of_week(local)
    awake_now = conf.mode == "learn" and scores(db, local, conf.zone())[hour] >= AWAKE_THRESHOLD
    return State("awake" if awake_now or recent else "idle", "outside the dream window", window)


def heatmap(db: Database, now: datetime | None = None) -> list[list[float]]:
    """Scores as 7 rows (Monday first) of 24 hours."""
    values = scores(db, now)
    return [values[day * 24 : day * 24 + 24] for day in range(7)]
