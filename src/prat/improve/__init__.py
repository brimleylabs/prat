"""Self-improvement: PRAT working on its own source code.

Off by default. While it is on, PRAT runs cycles that diagnose how it has
been doing, research current practice, file the best findings as cards on its
own repository, and land the work that can be proven.

Modules:

``settings``
    The toggle and its limits, stored in ``app_settings``.
``events``
    The activity feed: one row for every decision the loop makes.
"""

from prat.improve.events import EventKind, recent_events, record_event
from prat.improve.settings import (
    ImproveMode,
    ImproveSettings,
    load_improve_settings,
    update_improve_settings,
)

__all__ = [
    "EventKind",
    "ImproveMode",
    "ImproveSettings",
    "load_improve_settings",
    "recent_events",
    "record_event",
    "update_improve_settings",
]
