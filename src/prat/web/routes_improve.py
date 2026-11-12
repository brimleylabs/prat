"""The self-improvement page: the toggle, mode, limits, findings and the feed.

Every change goes through the same functions as ``prat improve`` and is written
to the activity feed with the actor ``web``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prat.db import Card, ImproveCycle, ImproveEvent, ImproveFinding, Project
from prat.domain import Effort, FindingStatus, Severity
from prat.improve.cycle import last_cycle_started, open_self_cards, spent_today
from prat.improve.events import EventKind, recent_events
from prat.improve.findings import allow_screened, dismiss_finding, is_screened, list_findings
from prat.improve.scheduler import may_start
from prat.improve.selfproject import (
    SelfInfo,
    SelfProjectError,
    get_self_project,
    load_self_info,
    setup_self_project,
)
from prat.improve.settings import ImproveMode, ImproveSettings, load_improve_settings
from prat.improve.settings import update_improve_settings as _update
from prat.overlay import (
    OverlayError,
    init_overlay,
    load_overlay,
    overlay_log,
    overlay_path,
    undo_overlay_commit,
)
from prat.web import views
from prat.web.app import render, web_context
from prat.web.routes_actions import _flash, _go, _text

router = APIRouter()

FEED_LIMIT = 40
OPEN_FINDINGS = (FindingStatus.NEW, FindingStatus.CARDED)

# Event kind to (label, tone) for the feed.
EVENT_LOOK: dict[str, tuple[str, str]] = {
    EventKind.TOGGLED: ("Toggled", "neutral"),
    EventKind.MODE_CHANGED: ("Mode", "neutral"),
    EventKind.SETTINGS_CHANGED: ("Settings", "neutral"),
    EventKind.SETUP: ("Setup", "neutral"),
    EventKind.SYNC_OK: ("Synced", "neutral"),
    EventKind.SYNC_DIVERGED: ("Out of sync", "warn"),
    EventKind.SYNC_FAILED: ("Sync failed", "bad"),
    EventKind.CYCLE_STARTED: ("Cycle", "neutral"),
    EventKind.CYCLE_FINISHED: ("Cycle", "ok"),
    EventKind.CYCLE_SKIPPED: ("Cycle", "neutral"),
    EventKind.CYCLE_FAILED: ("Cycle failed", "bad"),
    EventKind.CYCLE_CANCELLED: ("Cycle", "warn"),
    EventKind.RESEARCH_REJECTED: ("Research", "warn"),
    EventKind.FINDING_RECORDED: ("Finding", "neutral"),
    EventKind.FINDING_DISMISSED: ("Finding", "neutral"),
    EventKind.FINDING_STALE: ("Finding", "neutral"),
    EventKind.CARD_FILED: ("Card filed", "accent"),
    EventKind.TRIAL_PASSED: ("Trial passed", "ok"),
    EventKind.TRIAL_FAILED: ("Trial failed", "bad"),
    EventKind.LANDING_REFUSED: ("Not landed", "warn"),
    EventKind.AWAITING_APPROVAL: ("Needs you", "accent"),
    EventKind.CARD_REWORKED: ("Rework", "warn"),
    EventKind.CARD_ABANDONED: ("Abandoned", "bad"),
    EventKind.CARD_LANDED: ("Landed", "ok"),
    EventKind.MERGE_UNDONE: ("Undone", "warn"),
    EventKind.PUSH_OK: ("Pushed", "ok"),
    EventKind.PUSH_FAILED: ("Push failed", "bad"),
}

# Settings a person can change here, with their labels and help.
EDITABLE: tuple[tuple[str, str, str], ...] = (
    ("interval_minutes", "Minutes between cycles", "How often a scheduled cycle may start."),
    ("max_open_cards", "Open cards at most", "No new cards while this many are unfinished."),
    ("max_new_cards_per_cycle", "New cards per cycle", "Findings filed as cards each cycle."),
    ("max_reworks", "Reworks before giving up", "Automatic retries before a card is abandoned."),
    ("retry_cooldown_days", "Days before retrying", "How long an abandoned finding waits."),
    ("window_days", "Look-back days", "How far back the diagnosis reads runs and errors."),
    ("daily_budget_usd", "Daily guard (API value)", "Cycles pause for the day past this."),
    ("card_budget_usd", "Per-card guard (API value)", "A self card stops past this."),
)
SWITCHES: tuple[tuple[str, str, str], ...] = (
    ("research_enabled", "Research", "Look up current practice on the web while diagnosing."),
    ("push_enabled", "Push landed work", "Off keeps landed changes in the local clone only."),
)


@dataclass
class FeedItem:
    created_at: datetime | None
    label: str
    tone: str
    message: str
    card_id: int | None


@dataclass
class ImprovePage:
    conf: ImproveSettings
    project: Project | None
    info: SelfInfo | None
    last_cycle: ImproveCycle | None
    next_reason: str
    next_at: datetime | None
    guard_used: float
    open_cards: list[Card]
    counts: dict[str, int]
    findings: list[ImproveFinding]
    held: list[ImproveFinding]
    feed: list[FeedItem]
    cycle_running: bool

    @property
    def ready(self) -> bool:
        return self.project is not None


def _feed(events: list[ImproveEvent]) -> list[FeedItem]:
    items = []
    for event in events:
        label, tone = EVENT_LOOK.get(event.kind, (event.kind.replace("_", " "), "neutral"))
        items.append(
            FeedItem(event.created_at, label, tone, views.redact(event.message), event.card_id)
        )
    return items


def load_page(session: Session, *, next_reason: str, cycle_running: bool) -> ImprovePage:
    conf = load_improve_settings(session)
    now = datetime.now(UTC)
    last = session.scalars(select(ImproveCycle).order_by(ImproveCycle.id.desc())).first()
    started = views.utc(last_cycle_started(session))
    next_at = None
    if conf.enabled and started is not None:
        next_at = started + timedelta(minutes=conf.interval_minutes)
    counts = dict(
        session.execute(
            select(ImproveFinding.status, func.count()).group_by(ImproveFinding.status)
        ).all()
    )
    findings = [f for f in list_findings(session, limit=100) if f.status in OPEN_FINDINGS]
    findings.sort(key=lambda f: (Severity(f.severity).rank, Effort(f.effort).rank, -f.seen_count))
    spent = spent_today(session, now)
    return ImprovePage(
        conf=conf,
        project=get_self_project(session),
        info=load_self_info(session),
        last_cycle=last,
        next_reason=next_reason,
        next_at=next_at,
        guard_used=min(100.0, 100 * spent / conf.daily_budget_usd) if conf.daily_budget_usd else 0,
        open_cards=open_self_cards(session),
        counts={str(k): int(v) for k, v in counts.items()},
        findings=findings[:30],
        held=[
            f
            for f in list_findings(session, status=FindingStatus.DISMISSED, limit=50)
            if is_screened(f)
        ][:10],
        feed=_feed(recent_events(session, limit=FEED_LIMIT)),
        cycle_running=cycle_running,
    )


@router.get("/improve")
async def improve_page(request: Request) -> Response:
    ctx = web_context(request)
    decision = await asyncio.to_thread(may_start, ctx.db)
    running = ctx.improve is not None and ctx.improve.cycle_running

    def load() -> ImprovePage:
        with ctx.db.session() as session:
            page = load_page(session, next_reason=decision.reason, cycle_running=running)
            # Keep what was loaded readable after the session closes.
            session.expunge_all()
            return page

    page = await asyncio.to_thread(load)
    home = ctx.settings.home
    overlay = await asyncio.to_thread(load_overlay, overlay_path(home))
    changes = await asyncio.to_thread(overlay_log, home, 8)
    return render(
        request,
        "improve.html",
        {
            "page": page,
            "overlay": overlay,
            "overlay_changes": changes,
            "editable": EDITABLE,
            "switches": SWITCHES,
            "modes": [str(m) for m in ImproveMode],
            "scheduler": ctx.improve is not None,
        },
        nav="improve",
    )


def _change(request: Request, **changes: Any) -> str | None:
    ctx = web_context(request)
    try:
        with ctx.db.session() as session:
            _update(session, actor="web", **changes)
    except ValueError as exc:
        return str(exc)
    return None


@router.post("/improve/toggle")
async def improve_toggle(request: Request) -> Response:
    form = await request.form()
    enabled = _text(form, "enabled") == "1"
    error = await asyncio.to_thread(_change, request, enabled=enabled)
    if error:
        _flash(request, error, "bad")
    else:
        _flash(request, f"Self-improvement is {'on' if enabled else 'off'}.", "ok")
    return _go(request, "/improve")


@router.post("/improve/mode")
async def improve_mode(request: Request) -> Response:
    form = await request.form()
    error = await asyncio.to_thread(_change, request, mode=_text(form, "mode"))
    _flash(request, error or "Mode saved.", "bad" if error else "ok")
    return _go(request, "/improve")


@router.post("/improve/target")
async def improve_target(request: Request) -> Response:
    form = await request.form()
    error = await asyncio.to_thread(_change, request, target=_text(form, "target"))
    _flash(
        request,
        error or "Saved. It applies the next time self-improvement is set up.",
        "bad" if error else "ok",
    )
    return _go(request, "/improve")


@router.post("/improve/settings")
async def improve_settings(request: Request) -> Response:
    form = await request.form()
    changes: dict[str, Any] = {}
    for name, _label, _help in EDITABLE:
        value = _text(form, name)
        if value:
            changes[name] = value
    for name, _label, _help in SWITCHES:
        changes[name] = form.get(name) == "1"
    error = await asyncio.to_thread(_change, request, **changes)
    _flash(request, error or "Limits saved.", "bad" if error else "ok")
    return _go(request, "/improve")


@router.post("/improve/setup")
async def improve_setup(request: Request) -> Response:
    ctx = web_context(request)
    try:
        result = await asyncio.to_thread(setup_self_project, ctx.db, ctx.settings, actor="web")
    except SelfProjectError as exc:
        _flash(request, str(exc), "bad")
        return _go(request, "/improve")
    message = f"Self project {'created' if result.created else 'updated'} at {result.path}."
    for warning in result.warnings:
        message += f" {warning}"
    _flash(request, message, "warn" if result.warnings else "ok")
    return _go(request, "/improve")


@router.post("/improve/run")
async def improve_run(request: Request) -> Response:
    ctx = web_context(request)
    if ctx.improve is None:
        _flash(request, "This server runs without the self-improvement scheduler.", "bad")
    elif ctx.improve.start_manual():
        _flash(request, "Cycle started. Its steps appear in the activity feed.", "ok")
    else:
        _flash(request, "A cycle is already running.", "warn")
    return _go(request, "/improve")


@router.post("/improve/findings/{finding_id}/dismiss")
async def improve_dismiss(request: Request, finding_id: int) -> Response:
    ctx = web_context(request)
    form = await request.form()

    def dismiss() -> str | None:
        try:
            with ctx.db.session() as session:
                dismiss_finding(session, finding_id, note=_text(form, "note"), actor="web")
        except (LookupError, ValueError) as exc:
            return str(exc)
        return None

    error = await asyncio.to_thread(dismiss)
    _flash(request, error or f"Finding {finding_id} dismissed.", "bad" if error else "ok")
    return _go(request, "/improve")


@router.post("/improve/findings/{finding_id}/allow")
async def improve_allow(request: Request, finding_id: int) -> Response:
    ctx = web_context(request)

    def allow() -> str | None:
        try:
            with ctx.db.session() as session:
                allow_screened(session, finding_id, actor="web")
        except (LookupError, ValueError) as exc:
            return str(exc)
        return None

    error = await asyncio.to_thread(allow)
    _flash(
        request,
        error or f"Finding {finding_id} is back in the queue; a cycle can now file it as a card.",
        "bad" if error else "ok",
    )
    return _go(request, "/improve")


@router.post("/improve/overlay/init")
async def improve_overlay_init(request: Request) -> Response:
    ctx = web_context(request)
    settings = ctx.settings
    path = await asyncio.to_thread(
        lambda: init_overlay(
            settings.home,
            author_name=settings.git_author_name,
            author_email=settings.git_author_email,
        )
    )
    _flash(request, f"Overlay ready at {path}.", "ok")
    return _go(request, "/improve#overlay")


@router.post("/improve/overlay/undo/{sha}")
async def improve_overlay_undo(request: Request, sha: str) -> Response:
    ctx = web_context(request)
    if not all(c in "0123456789abcdef" for c in sha) or not 7 <= len(sha) <= 40:
        _flash(request, "That is not a commit id.", "bad")
        return _go(request, "/improve#overlay")
    try:
        new = await asyncio.to_thread(undo_overlay_commit, ctx.settings.home, sha)
    except OverlayError as exc:
        _flash(request, str(exc), "bad")
    else:
        _flash(request, f"Overlay change {sha[:10]} undone as {new[:10]}.", "ok")
    return _go(request, "/improve#overlay")
