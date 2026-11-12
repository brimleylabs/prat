"""Rhythm and dreams in the web UI: when PRAT thinks you work, and what it did while you did not."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from prat import rhythm
from prat.db.models import Dream
from prat.dream import recent_dreams, run_dream
from prat.web.app import render, web_context
from prat.web.routes_actions import _flash, _go, _text

router = APIRouter()
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass
class HeatCell:
    day: int
    hour: int
    score: float
    level: int
    in_window: bool


@dataclass
class RhythmPanel:
    state: rhythm.State
    settings: rhythm.RhythmSettings
    cells: list[HeatCell] = field(default_factory=list)
    active_days: int = 0
    dreams: list[Dream] = field(default_factory=list)
    dreaming: bool = False
    now_cell: tuple[int, int] = (0, 0)

    @property
    def learning(self) -> bool:
        return self.settings.mode == "learn" and self.active_days < rhythm.MIN_ACTIVE_DAYS


def _window_hours(window: rhythm.Window | None, zone: Any) -> set[tuple[int, int]]:
    hours: set[tuple[int, int]] = set()
    if window is None:
        return hours
    moment = window.start.astimezone(zone)
    while moment < window.end.astimezone(zone):
        hours.add((moment.weekday(), moment.hour))
        moment = moment.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return hours


def load_panel(request: Request) -> RhythmPanel:
    ctx = web_context(request)
    db = ctx.db
    conf = rhythm.load_settings(db)
    zone = conf.zone()
    current = rhythm.state(db)
    window_hours = _window_hours(current.window, zone)
    heat = rhythm.heatmap(db)
    cells = [
        HeatCell(
            day,
            hour,
            score,
            0 if score <= 0 else min(4, 1 + int(score * 4)),
            (day, hour) in window_hours,
        )
        for day, row in enumerate(heat)
        for hour, score in enumerate(row)
    ]
    local = datetime.now(zone)
    return RhythmPanel(
        state=current,
        settings=conf,
        cells=cells,
        active_days=rhythm.active_days(db),
        dreams=recent_dreams(db, 6),
        dreaming=bool(ctx.dreams and ctx.dreams.dreaming)
        or any(d.status == "running" for d in recent_dreams(db, 1)),
        now_cell=(local.weekday(), local.hour),
    )


@router.get("/improve/rhythm")
async def rhythm_panel(request: Request) -> Response:
    panel = await asyncio.to_thread(load_panel, request)
    return render(request, "_rhythm.html", {"panel": panel, "days": DAYS}, nav="improve")


@router.post("/improve/rhythm/hours")
async def rhythm_hours(request: Request) -> Response:
    form = await request.form()
    db = web_context(request).db

    def hour(name: str, default: int) -> int:
        with contextlib.suppress(ValueError):
            return int(_text(form, name)) % 24
        return default

    settings = rhythm.RhythmSettings(
        mode=_text(form, "mode") or "learn",
        timezone=_text(form, "timezone"),
        fixed_start=hour("fixed_start", 1),
        fixed_end=hour("fixed_end", 5),
    )
    try:
        await asyncio.to_thread(rhythm.save_settings, db, settings)
        _flash(request, "Dream hours saved.", "ok")
    except ValueError as exc:
        _flash(request, f"Not saved: {exc}", "bad")
    return _go(request, "/improve#rhythm")


@router.post("/improve/dream")
async def dream_now(request: Request) -> Response:
    ctx = web_context(request)
    if ctx.dreams is not None:
        started = ctx.dreams.start_now()
    else:
        task = asyncio.create_task(
            run_dream(
                ctx.db,
                ctx.settings,
                ctx.engine.runtime,
                trigger="manual",
                runtime_name=ctx.runtime_name,
                forced=True,
            )
        )
        request.app.state.manual_dream = task
        started = True
    if started:
        _flash(request, "Dreaming now. It stops if a card of yours starts.", "ok")
    else:
        _flash(request, "A dream is already running.", "info")
    return _go(request, "/improve#rhythm")
