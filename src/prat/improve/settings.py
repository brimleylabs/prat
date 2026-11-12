"""Self-improvement settings.

They live in the ``app_settings`` table under keys prefixed ``improve.`` so
they can change while PRAT runs: the web page and the CLI write them, the
scheduler reads them before every decision. Nothing is cached, so turning the
toggle off takes effect at the next check.

A stored value that no longer validates (written by an older or newer
version, or edited by hand) is ignored and the default is used for that key.
A bad row can therefore never switch the loop on or lift a limit.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from prat.db.models import AppSetting
from prat.improve.events import EventKind, record_event

log = logging.getLogger(__name__)

PREFIX = "improve."


class ImproveMode(StrEnum):
    """``auto``: PRAT lands and pushes every proven change itself.
    ``review``: the same proof runs, and a person approves before the merge."""

    AUTO = "auto"
    REVIEW = "review"


class ImproveSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # The toggle. Nothing runs while it is false.
    enabled: bool = False
    # What the loop improves: the installation's overlay (rules, guidance and
    # settings, kept apart from PRAT), or PRAT's own source, for its maintainers.
    target: Literal["overlay", "core"] = "overlay"
    mode: ImproveMode = ImproveMode.AUTO
    # Automatic rework attempts before a self card is abandoned.
    max_reworks: int = Field(default=2, ge=0, le=10)
    # How long an abandoned finding waits before it may be tried again.
    retry_cooldown_days: int = Field(default=30, ge=0, le=3650)
    # Time between scheduled cycles.
    interval_minutes: int = Field(default=360, ge=5, le=60 * 24 * 30)
    # Cap on estimated spend per UTC day across research and self cards.
    daily_budget_usd: float = Field(default=5.0, ge=0, le=10_000)
    # Budget for each self card.
    card_budget_usd: float = Field(default=2.0, gt=0, le=1_000)
    # No new self cards while this many are not finished.
    max_open_cards: int = Field(default=3, ge=1, le=50)
    max_new_cards_per_cycle: int = Field(default=2, ge=1, le=20)
    research_enabled: bool = True
    # Push after landing. When false, work is merged locally only.
    push_enabled: bool = True
    # Diagnostic look-back.
    window_days: int = Field(default=7, ge=1, le=365)


FIELD_NAMES: frozenset[str] = frozenset(ImproveSettings.model_fields)


def _stored(session: Session) -> dict[str, Any]:
    rows = session.scalars(select(AppSetting).where(AppSetting.key.like(f"{PREFIX}%")))
    values: dict[str, Any] = {}
    for row in rows:
        name = row.key[len(PREFIX) :]
        if name in FIELD_NAMES:
            values[name] = row.value
    return values


def load_improve_settings(session: Session) -> ImproveSettings:
    """The current settings: stored values over the defaults."""
    values = _stored(session)
    for _attempt in range(len(values) + 1):
        try:
            return ImproveSettings.model_validate(values)
        except ValidationError as exc:
            bad = {str(error["loc"][0]) for error in exc.errors() if error["loc"]}
            if not bad & set(values):
                break
            for name in bad:
                if name in values:
                    log.warning("ignoring stored setting %s%s: it is not valid", PREFIX, name)
                    del values[name]
    return ImproveSettings()


def update_improve_settings(
    session: Session, *, actor: str = "person", **changes: Any
) -> ImproveSettings:
    """Validate and store ``changes``. Raises ``ValueError`` and stores nothing if
    any value is not allowed. Every real change is written to the activity feed
    with ``actor`` (who or what made it: ``cli``, ``web``...).
    """
    unknown = sorted(set(changes) - FIELD_NAMES)
    if unknown:
        raise ValueError(f"unknown self-improvement setting: {', '.join(unknown)}")
    before = load_improve_settings(session)
    try:
        after = ImproveSettings.model_validate({**before.model_dump(), **changes}, strict=False)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ValueError(f"invalid self-improvement setting: {problems}") from exc
    old, new = before.model_dump(mode="json"), after.model_dump(mode="json")
    changed = {name: new[name] for name in changes if old[name] != new[name]}
    for name, value in changed.items():
        row = session.get(AppSetting, PREFIX + name)
        if row is None:
            session.add(AppSetting(key=PREFIX + name, value=value))
        else:
            row.value = value
    session.flush()
    if "enabled" in changed:
        state = "on" if after.enabled else "off"
        record_event(
            session,
            EventKind.TOGGLED,
            f"self-improvement turned {state} by {actor}",
            data={"enabled": after.enabled, "actor": actor},
        )
    if "mode" in changed:
        record_event(
            session,
            EventKind.MODE_CHANGED,
            f"mode set to {after.mode.value} by {actor}",
            data={"from": old["mode"], "to": new["mode"], "actor": actor},
        )
    rest = {name: value for name, value in changed.items() if name not in ("enabled", "mode")}
    if rest:
        record_event(
            session,
            EventKind.SETTINGS_CHANGED,
            f"settings changed by {actor}: {', '.join(sorted(rest))}",
            data={
                "from": {name: old[name] for name in rest},
                "to": rest,
                "actor": actor,
            },
        )
    return after
