"""The agent wall: one live tile per running agent, and a full-screen view of one.

Tiles read only the ends of each journal, so the wall stays cheap to refresh
however long a session runs. The full-screen view uses the card's own event
stream, the same one the card page uses.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from prat import store
from prat.domain import CardStatus, RunStatus, StepStatus
from prat.engine import Engine
from prat.journal import read_first_and_last
from prat.web import data, views
from prat.web.app import render, web_context

router = APIRouter()

# What a tile shows: what the agent says and does, not the engine's bookkeeping.
TILE_CATEGORIES = frozenset({"text", "tool", "denied", "verify"})
TILE_LINES = 6
SCAN_ENTRIES = 60


@dataclass
class WallTile:
    card_id: int
    title: str
    project_slug: str
    status: str
    queued: bool
    step: str | None
    since: datetime | None
    run_id: int | None
    lines: list[views.EntryView] = field(default_factory=list)
    denials: int = 0


def _tile_lines(path: str | None) -> tuple[list[views.EntryView], int]:
    if not path:
        return [], 0
    first, last = read_first_and_last(Path(path), SCAN_ENTRIES)
    start = first.ts if first is not None else None
    shown = [views.present(entry, start) for entry in last]
    denials = sum(1 for e in shown if e.category == "denied")
    return [e for e in shown if e.category in TILE_CATEGORIES][-TILE_LINES:], denials


def load_wall(session: Session, engine: Engine) -> list[WallTile]:
    """Running agents first, then queued ones. Reads journal files; run it in a thread."""
    scheduled = engine.scheduled_cards()
    tiles = []
    for card in store.list_cards(session):
        running = card.status == CardStatus.RUNNING
        queued = card.id in scheduled and not running
        if not (running or queued):
            continue
        run = store.latest_run(session, card)
        live = run is not None and run.status == RunStatus.RUNNING
        step = None
        if live and run is not None:
            step = next((str(s.name) for s in run.steps if s.status == StepStatus.RUNNING), None)
        lines, denials = _tile_lines(run.journal_path if live and run else None)
        tiles.append(
            WallTile(
                card_id=card.id,
                title=card.title,
                project_slug=card.project.slug,
                status=str(card.status),
                queued=queued,
                step=step,
                since=run.started_at if live and run else None,
                run_id=run.id if live and run else None,
                lines=lines,
                denials=denials,
            )
        )
    tiles.sort(key=lambda t: (t.queued, t.card_id))
    return tiles


async def _tiles(request: Request) -> list[WallTile]:
    ctx = web_context(request)

    def load() -> list[WallTile]:
        with ctx.db.session() as session:
            return load_wall(session, ctx.engine)

    return await asyncio.to_thread(load)


@router.get("/wall")
async def wall(request: Request) -> Response:
    return render(request, "wall.html", {"tiles": await _tiles(request)}, nav="wall")


@router.get("/wall/tiles")
async def wall_tiles(request: Request) -> Response:
    return render(request, "_wall_tiles.html", {"tiles": await _tiles(request)}, nav="wall")


@router.get("/wall/{card_id}")
async def wall_focus(request: Request, card_id: int) -> Response:
    """One agent's session, full screen and live."""
    ctx = web_context(request)

    def load() -> data.CardPage:
        with ctx.db.session() as session:
            page = data.load_card(session, ctx.engine, card_id)
            session.expunge_all()
            return page

    try:
        page = await asyncio.to_thread(load)
    except store.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    step = None
    if page.run is not None:
        step = next((s.name for s in page.run.steps if s.status == StepStatus.RUNNING), None)
    return render(
        request,
        "wall_focus.html",
        {"page": page, "card": page.card, "step": step},
        nav="wall",
    )
