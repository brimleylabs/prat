"""The crew page: who works here, in their own words, and a place to talk with them.

``/crew`` lists every agent by the name it chose, with its bio, personality, drive and record;
``/crew/<agent>`` is a conversation with one of them; a stand-up asks every agent that worked in
the last day for its report. Conversations never interrupt an agent's work (prat.converse).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from prat import converse, crew, identity, memory
from prat.model_registry import Role, resolve_model
from prat.web.app import current_session, render, web_context

router = APIRouter()
STANDUP_FILE = "standup.json"


@dataclass
class AgentCard:
    key: str
    name: str
    role: str
    bio: str
    personality: str
    drive: str
    chosen: bool
    # The avatar's colour class (app.css .av-*): the lead's stack, else "other". The page's
    # content security policy allows no inline styles.
    tone: str
    episodes: int
    landed: int
    reworked: int
    mood: float
    working_on: list[int] = field(default_factory=list)


def _cards(home: Any, db: Any) -> list[AgentCard]:
    cards = []
    for key in memory.all_agents(home):
        who = identity.of(home, key)
        stats = memory.AgentMemory(home, key).stats()
        member = crew.MEMBERS.get(key)
        cards.append(
            AgentCard(
                key=key,
                name=who.name,
                role=who.role,
                bio=who.bio if who.chosen else "",
                personality=who.personality if who.chosen else "",
                drive=who.drive,
                chosen=who.chosen,
                tone=member.key if member else "other",
                episodes=stats["episodes"],
                landed=stats["outcomes"].get("landed", 0),
                reworked=stats["outcomes"].get("reworked", 0),
                mood=stats["mood"],
                working_on=[w["card"] for w in converse.at_work(db, key)],
            )
        )
    return cards


def _flash(request: Request, message: str, tone: str = "info") -> None:
    session = current_session(request)
    if session is not None:
        session.flash(message, tone)


@router.get("/crew")
async def crew_page(request: Request) -> Response:
    ctx = web_context(request)
    cards = await asyncio.to_thread(_cards, ctx.settings.home, ctx.db)
    standup = None
    with contextlib.suppress(OSError, ValueError):
        standup = json.loads(
            (ctx.settings.home / memory.AGENTS_DIR / STANDUP_FILE).read_text("utf-8")
        )
    return render(request, "crew.html", {"cards": cards, "standup": standup}, nav="crew")


@router.get("/crew/{key}")
async def agent_page(request: Request, key: str) -> Response:
    ctx = web_context(request)
    home = ctx.settings.home
    if (
        key not in memory.all_agents(home)
        and key not in crew.MEMBERS
        and key not in crew.HELPER_PROMPTS
    ):
        raise HTTPException(404, "no such agent")
    who = identity.of(home, key)
    turns = converse.history(home, key, limit=40)
    beliefs = sorted(
        memory.AgentMemory(home, key).beliefs(), key=lambda b: b.strength, reverse=True
    )
    # Newest first by when it happened: inherited episodes are added later but are older.
    episodes = sorted(memory.AgentMemory(home, key).episodes(), key=lambda e: e.at)[-8:][::-1]
    work = await asyncio.to_thread(converse.at_work, ctx.db, key)
    return render(
        request,
        "crew_agent.html",
        {
            "who": who,
            "key": key,
            "turns": turns,
            "beliefs": beliefs[:10],
            "episodes": episodes,
            "work": work,
            "record": memory.track_record(memory.AgentMemory(home, key), reader="person"),
            "clock": lambda t: time.strftime("%b %d %H:%M", time.localtime(t)),
        },
        nav="crew",
    )


@router.post("/crew/{key}/talk")
async def talk(request: Request, key: str, message: str = Form("")) -> Response:
    ctx = web_context(request)
    text = message.strip()
    if text:
        model = resolve_model(Role.CREW, settings_models=ctx.settings.models)
        try:
            await converse.talk(
                ctx.settings.home, ctx.db, ctx.engine.runtime, key, text, model=model
            )
        except Exception as exc:
            _flash(request, f"They could not answer: {exc}", "bad")
    return RedirectResponse(f"/crew/{key}#latest", status_code=303)


@router.post("/crew/{key}/introduce")
async def introduce(request: Request, key: str) -> Response:
    ctx = web_context(request)
    model = resolve_model(Role.CREW, settings_models=ctx.settings.models)
    who = await identity.introduce(ctx.settings.home, ctx.engine.runtime, key, model=model)
    _flash(request, f"Meet {who.name}.", "ok")
    return RedirectResponse(f"/crew/{key}", status_code=303)


@router.post("/crew/standup")
async def standup(request: Request) -> Response:
    ctx = web_context(request)
    model = resolve_model(Role.CREW, settings_models=ctx.settings.models)
    reports = await converse.standup(ctx.settings.home, ctx.db, ctx.engine.runtime, model=model)
    path = ctx.settings.home / memory.AGENTS_DIR / STANDUP_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "at": time.strftime("%A %H:%M"),
                "reports": [{"key": r.key, "name": r.name, "text": r.text} for r in reports],
            }
        ),
        encoding="utf-8",
    )
    if not reports:
        _flash(request, "Nobody has worked in the last day.", "info")
    return RedirectResponse("/crew#standup", status_code=303)
