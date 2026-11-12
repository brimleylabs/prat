"""The self-improvement activity feed.

Every decision the loop makes is written here: a cycle starting, a finding
recorded, a card filed, a landing refused and why, a push. The feed is what a
person reads to see what PRAT did to itself while nobody was watching, so
nothing in the loop changes state without a row.

Messages and data pass through the same redaction as the error log, and text
that came from the web is stored as text and escaped wherever it is shown.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from prat.db.models import ImproveEvent
from prat.errors import redact_detail, redact_text

MAX_MESSAGE_CHARS = 2000


class EventKind(StrEnum):
    # Control
    TOGGLED = "toggled"
    MODE_CHANGED = "mode_changed"
    SETTINGS_CHANGED = "settings_changed"
    # The self project
    SETUP = "setup"
    SYNC_OK = "sync_ok"
    SYNC_DIVERGED = "sync_diverged"
    SYNC_FAILED = "sync_failed"
    # Cycles
    CYCLE_STARTED = "cycle_started"
    CYCLE_FINISHED = "cycle_finished"
    CYCLE_SKIPPED = "cycle_skipped"
    CYCLE_FAILED = "cycle_failed"
    CYCLE_CANCELLED = "cycle_cancelled"
    RESEARCH_REJECTED = "research_rejected"
    # Findings and cards
    FINDING_RECORDED = "finding_recorded"
    FINDING_DISMISSED = "finding_dismissed"
    FINDING_STALE = "finding_stale"
    CARD_FILED = "card_filed"
    # Landing
    TRIAL_PASSED = "trial_passed"
    TRIAL_FAILED = "trial_failed"
    LANDING_REFUSED = "landing_refused"
    AWAITING_APPROVAL = "awaiting_approval"
    CARD_REWORKED = "card_reworked"
    CARD_ABANDONED = "card_abandoned"
    CARD_LANDED = "card_landed"
    MERGE_UNDONE = "merge_undone"
    PUSH_OK = "push_ok"
    PUSH_FAILED = "push_failed"


def record_event(
    session: Session,
    kind: EventKind,
    message: str = "",
    *,
    cycle_id: int | None = None,
    card_id: int | None = None,
    data: Mapping[str, Any] | None = None,
) -> ImproveEvent:
    """Add one row to the feed. The caller's session decides when it is committed."""
    text = redact_text(message)
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[: MAX_MESSAGE_CHARS - 3] + "..."
    safe = redact_detail(dict(data or {}))
    event = ImproveEvent(
        kind=EventKind(kind).value,
        message=text,
        cycle_id=cycle_id,
        card_id=card_id,
        data=safe if isinstance(safe, dict) else {},
    )
    session.add(event)
    session.flush()
    return event


def recent_events(
    session: Session,
    *,
    limit: int = 50,
    cycle_id: int | None = None,
    card_id: int | None = None,
    kinds: tuple[EventKind, ...] = (),
) -> list[ImproveEvent]:
    """The newest events first."""
    query = select(ImproveEvent).order_by(ImproveEvent.id.desc()).limit(max(1, limit))
    if cycle_id is not None:
        query = query.where(ImproveEvent.cycle_id == cycle_id)
    if card_id is not None:
        query = query.where(ImproveEvent.card_id == card_id)
    if kinds:
        query = query.where(ImproveEvent.kind.in_([kind.value for kind in kinds]))
    return list(session.scalars(query))
