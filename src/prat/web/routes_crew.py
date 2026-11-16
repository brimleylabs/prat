"""The crew page: who works here, in their own words, and a place to talk with them.

``/crew`` lists every agent by the name it chose, with its bio, personality, drive and record;
``/crew/<agent>`` is a conversation with one of them; a stand-up asks every agent that worked in
the last day for its report. Conversations never interrupt an agent's work (prat.converse).
``/ideas`` is the crew's ideas for working better, to accept or decline (prat.ideas).
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
from sqlalchemy import select

from prat import continuity, converse, crew, identity, memory, social
from prat.db import Project
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
    from prat import ideas

    waiting = len(ideas.waiting(ctx.settings.home))
    return render(
        request,
        "crew.html",
        {"cards": cards, "standup": standup, "ideas_waiting": waiting},
        nav="crew",
    )


@router.get("/crew/social")
async def social_page(request: Request) -> Response:
    """Every social hour, newest first, in full: the crew's conversations and the person's
    good mornings."""
    ctx = web_context(request)
    home = ctx.settings.home
    found = await asyncio.to_thread(social.gatherings, home, 30)
    return render(
        request,
        "crew_social.html",
        {
            "gatherings": found,
            "names": {k: identity.of(home, k).name for g in found for k in g.participants},
            "person": social.PERSON,
            "today": social.today(home),
            "clock": lambda t: time.strftime("%A %b %d, %H:%M", time.localtime(t)),
            "hour": lambda t: time.strftime("%H:%M", time.localtime(t)),
        },
        nav="crew",
    )


@router.post("/crew/social/hello")
async def social_hello(request: Request, message: str = Form("")) -> Response:
    ctx = web_context(request)
    model = resolve_model(Role.CREW, settings_models=ctx.settings.models)
    try:
        await social.say_hello(ctx.settings.home, ctx.engine.runtime, message, model=model)
    except ValueError as exc:
        _flash(request, str(exc), "warn")
    except Exception as exc:
        _flash(request, f"They could not answer: {exc}", "bad")
    return RedirectResponse("/crew/social#latest", status_code=303)


@router.post("/crew/social/now")
async def social_now(request: Request) -> Response:
    ctx = web_context(request)
    model = resolve_model(Role.CREW, settings_models=ctx.settings.models)
    met = await social.gather(
        ctx.settings.home,
        ctx.engine.runtime,
        model=model,
        size=ctx.settings.social_size or 5,
        rounds=ctx.settings.social_rounds,
    )
    if met is None:
        _flash(request, "Fewer than two agents have introduced themselves; nobody met.", "info")
    return RedirectResponse("/crew/social#latest", status_code=303)


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
            "inner": social.inner_of(home, key),
            "story": continuity.story_of(home, key),
            "aspirations": continuity.aspirations_log(home, key)[-6:][::-1],
            "colleagues": {
                identity.of(home, k).name: v
                for k, v in social.inner_of(home, key).colleagues.items()
            },
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


@router.get("/ideas")
async def ideas_page(request: Request) -> Response:
    from prat import ideas

    ctx = web_context(request)
    home = ctx.settings.home
    found = ideas.all_ideas(home)
    found.sort(key=lambda i: i.status != "proposed")
    names = {i.agent: identity.of(home, i.agent).name for i in found}
    with ctx.db.session() as session:
        projects = sorted(session.scalars(select(Project.slug)).all())
    return render(
        request,
        "ideas.html",
        {
            "ideas": found,
            "names": names,
            "projects": projects,
            "waiting": sum(1 for i in found if i.status == "proposed"),
            "clock": lambda t: time.strftime("%b %d %H:%M", time.localtime(t)),
        },
        nav="crew",
    )


@router.post("/ideas/{idea_id}/decide")
async def decide_idea(
    request: Request,
    idea_id: str,
    decision: str = Form(""),
    note: str = Form(""),
    build: str = Form(""),
    project: str = Form(""),
) -> Response:
    from prat import ideas

    ctx = web_context(request)
    accept = decision == "accept"
    try:
        idea = await asyncio.to_thread(
            ideas.decide, ctx.settings.home, idea_id, accept=accept, note=note
        )
        if accept and build:
            card_id = await asyncio.to_thread(
                ideas.put_in_place, ctx.db, ctx.settings.home, idea.id, project=project or None
            )
            ctx.engine.submit(card_id)
            _flash(request, f"Accepted. Card #{card_id} puts it in place.", "ok")
        else:
            _flash(request, f"{'Accepted' if accept else 'Declined'}: {idea.title}", "ok")
    except (ideas.IdeaError, LookupError) as exc:
        _flash(request, str(exc), "warn")
    return RedirectResponse(f"/ideas#idea-{idea_id}", status_code=303)


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
