"""Alerts: the things worth interrupting a person for, worked out from mission control's snapshot.

* an agent asked a question (a blocked card);
* a job stalled (no progress for its ``stall_minutes``) or failed;
* an environment of the person's checkout stopped answering or exited;
* a card has waited in review longer than ``alert_review_hours``;
* a running agent has done nothing for ``alert_silent_minutes``;
* the service is refusing requests (rate limit).

Mission control shows them at the top. With ``alert_webhook`` set, the server also sends each
new one there once, as a plain-text POST (an ntfy topic works: it reaches a phone), and again
only if it clears and comes back.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prat.config import Settings

if TYPE_CHECKING:
    from prat.mission import Snapshot

log = logging.getLogger(__name__)
STATE_FILE = "alerts.json"


def from_snapshot(snap: Snapshot, settings: Settings) -> list[str]:
    from prat import governor

    found: list[str] = []
    for item in snap.waiting:
        if item.status == "blocked":
            found.append(f"#{item.card_id} {item.project} asks: {item.detail[:140]}")
    for job in snap.jobs:
        if job.stalled:
            found.append(
                f"job {job.name} (run {job.run_id}, {job.slug}) stalled: no progress lately"
            )
        elif job.status == "failed" and job.finished and time.time() - job.finished < 3600:
            found.append(f"job {job.name} (run {job.run_id}, {job.slug}) failed")
    for line in snap.projects:
        for env in line.environments:
            if env.state in ("unhealthy", "exited"):
                found.append(f"{line.slug} {env.name} is {env.state}")
    limit = settings.alert_review_hours
    if limit:
        for item, hours in zip(
            [w for w in snap.waiting if w.status == "review"],
            sorted(snap.review_waits, reverse=True),
            strict=False,
        ):
            if hours >= limit:
                found.append(f"#{item.card_id} has waited {hours:.0f}h for your review")
    if snap.ideas_waiting:
        count = len(snap.ideas_waiting)
        found.append(
            f"{count} idea{'s' if count != 1 else ''} from the crew waiting for you "
            "(prat idea list)"
        )
    silent = settings.alert_silent_minutes
    now = snap.taken_at
    for flight in snap.flights:
        if flight.queued or flight.since is None or not silent:
            continue
        last = flight.activity[-1].offset if flight.activity else 0.0
        quiet = (now - flight.since).total_seconds() - last
        if quiet > silent * 60:
            found.append(
                f"the agent on #{flight.card_id} has been quiet for {quiet / 60:.0f} minutes"
            )
    pressure = governor.pressure()
    if pressure.status == "rejected" and (pressure.resets_at or 0) > time.time():
        found.append("the service is refusing requests (rate limit); new work is held")
    return found


def _state(home: Path) -> dict[str, float]:
    try:
        return dict(json.loads((home / STATE_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return {}


def new_alerts(home: Path, current: list[str]) -> list[str]:
    """The alerts not already sent; remembers the current set (a cleared one can come back)."""
    seen = _state(home)
    fresh = [a for a in current if a not in seen]
    keep = {a: seen.get(a, time.time()) for a in current}
    (home / STATE_FILE).write_text(json.dumps(keep), encoding="utf-8")
    return fresh


def send(url: str, text: str, *, title: str = "PRAT") -> bool:
    import httpx

    try:
        response = httpx.post(
            url,
            content=text.encode("utf-8"),
            headers={"Title": title, "Content-Type": "text/plain; charset=utf-8"},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        log.warning("could not send an alert: %s", exc)
        return False
    return response.status_code < 400


async def watch(settings: Settings, db: Any, stop: asyncio.Event, every: float = 60.0) -> None:
    """The server's alert loop: every minute, send the new alerts to ``alert_webhook``."""
    from prat.mission import MissionData

    data = MissionData(settings, db)
    while not stop.is_set():
        try:
            if settings.alert_webhook:
                snap = await asyncio.to_thread(data.snapshot, None, datetime.now(UTC))
                for alert in await asyncio.to_thread(new_alerts, settings.home, snap.alerts):
                    await asyncio.to_thread(send, settings.alert_webhook, alert)
        except Exception:
            log.exception("alert check failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=every)
