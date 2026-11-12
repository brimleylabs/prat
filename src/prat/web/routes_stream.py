"""Server-sent event streams for live updates.

Streams poll the database and the run journal rather than relying on an
in-process bus, so changes made by the CLI or another PRAT process show up
too. Each stream ends after ``web_stream_seconds`` (browsers reconnect by
themselves); a card stream also ends once the card stops running.

Journal entries are sent as HTML rendered by the same autoescaping template
as the page, so nothing an agent wrote can become markup.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from prat import store
from prat.domain import CardStatus
from prat.journal import JournalTail
from prat.web import data, views
from prat.web.app import WebContext, web_context

router = APIRouter()

STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def sse(event: str, payload: str = "", event_id: str | None = None) -> str:
    lines = [f"event: {event}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    for part in payload.replace("\r", "").split("\n"):
        lines.append(f"data: {part}")
    return "\n".join(lines) + "\n\n"


def _stream(generator: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(generator, media_type="text/event-stream", headers=STREAM_HEADERS)


@router.get("/events/board")
async def board_events(request: Request) -> StreamingResponse:
    ctx = web_context(request)

    async def generate() -> AsyncIterator[str]:
        deadline = time.monotonic() + ctx.settings.web_stream_seconds
        last: tuple[Any, ...] | None = None
        yield "retry: 3000\n\n"
        while time.monotonic() < deadline:
            if await request.is_disconnected():
                return
            with ctx.db.session() as session:
                signature = (*data.board_signature(session), ctx.engine.scheduled_cards())
            if last is not None and signature != last:
                yield sse("board", "changed")
            last = signature
            await asyncio.sleep(ctx.settings.web_poll_seconds)

    return _stream(generate())


def _parse_last_event(value: str | None) -> tuple[int | None, int]:
    if not value or ":" not in value:
        return None, 0
    run_text, _, seq_text = value.partition(":")
    if run_text.isdigit() and seq_text.isdigit():
        return int(run_text), int(seq_text)
    return None, 0


def _render_entry(ctx: WebContext, view: views.EntryView) -> str:
    template = ctx.templates.env.get_template("_entry.html")
    return template.render(e=view)


@router.get("/events/cards/{card_id}")
async def card_events(
    request: Request, card_id: int, run: int | None = None, after: int = 0
) -> StreamingResponse:
    ctx = web_context(request)
    with ctx.db.session() as session:
        try:
            store.get_card(session, card_id)
        except store.NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
    last_run, last_seq = _parse_last_event(request.headers.get("last-event-id"))
    if last_run is not None:
        run, after = last_run, last_seq

    async def generate() -> AsyncIterator[str]:
        deadline = time.monotonic() + ctx.settings.web_stream_seconds
        current_run = run
        sent = max(after, 0)
        tail: JournalTail | None = None
        start: datetime | None = None
        last_signature: tuple[Any, ...] | None = None
        yield "retry: 2000\n\n"
        while time.monotonic() < deadline:
            if await request.is_disconnected():
                return
            with ctx.db.session() as session:
                card = store.get_card(session, card_id)
                latest = store.latest_run(session, card)
                signature = data.card_signature(session, ctx.engine, card_id)
                status = card.status
                latest_id = latest.id if latest else None
                journal = latest.journal_path if latest else None
            if latest_id is not None and latest_id != current_run:
                if current_run is not None:
                    yield sse("run", str(latest_id))
                    sent = 0
                current_run = latest_id
                tail, start = None, None
            if tail is None and journal:
                tail = JournalTail(Path(journal))
            if tail is not None:
                for entry in tail.read_new():
                    if start is None:
                        start = entry.ts
                    if entry.seq <= sent:
                        continue
                    view = views.present(entry, start)
                    yield sse("entry", _render_entry(ctx, view), f"{current_run}:{entry.seq}")
                    sent = entry.seq
            if last_signature is not None and signature != last_signature:
                yield sse("status", "changed")
            last_signature = signature
            if status != CardStatus.RUNNING and not ctx.engine.is_scheduled(card_id):
                yield sse("end", "finished")
                return
            await asyncio.sleep(ctx.settings.web_poll_seconds)

    return _stream(generate())
