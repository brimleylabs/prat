"""``GET /api/mission``: this installation's mission control snapshot, for another PRAT.

Another machine's ``prat mission`` shows this one on its deck (``remotes`` in its config).
The request carries this installation's access token as a bearer token; nothing else is
accepted, and nothing here changes anything.
"""

from __future__ import annotations

import asyncio
import hmac
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from prat import jobs
from prat.engine import ApproveError, EngineError
from prat.environments import EnvironmentError
from prat.mission import MissionData, to_json
from prat.web.app import web_context
from prat.web.tokens import read_access_token

router = APIRouter()


@router.get("/api/mission")
async def mission_snapshot(request: Request, client: str | None = None) -> Response:
    ctx = web_context(request)
    token = read_access_token(ctx.settings) or ""
    supplied = request.headers.get("authorization", "")
    expected = f"Bearer {token}"
    if not token or not hmac.compare_digest(supplied.encode(), expected.encode()):
        return JSONResponse({"error": "a bearer token is required"}, status_code=401)
    data = getattr(request.app.state, "mission_data", None)
    if data is None:
        data = request.app.state.mission_data = MissionData(ctx.settings, ctx.db)
    snapshot = await asyncio.to_thread(data.snapshot, client)
    return JSONResponse(to_json(snapshot))


# Actions from mission control (bearer token; see prat.web.security.BEARER_PREFIX) ----------


def _authorised(request: Request) -> bool:
    token = read_access_token(web_context(request).settings) or ""
    supplied = request.headers.get("authorization", "")
    return bool(token) and hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode())


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@router.post("/api/act/{action}")
async def act(request: Request, action: str) -> Response:
    """approve, rework (also answers a blocked card), job-start, job-stop, job-land,
    job-discard, env-restart. Runs in the server, whose engine runs the agents."""
    if not _authorised(request):
        return JSONResponse({"error": "a bearer token is required"}, status_code=401)
    ctx = web_context(request)
    body = await _body(request)
    try:
        message = await _act(ctx, action, body)
    except (
        EngineError,
        ApproveError,
        jobs.JobError,
        LookupError,
        ValueError,
        EnvironmentError,
    ) as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    return JSONResponse({"ok": True, "message": message})


def _decide_idea(ctx: Any, action: str, body: dict[str, Any]) -> tuple[str, int | None]:
    """Accept or decline an idea (prat.ideas); an accepted one with ``build`` gets a card that
    puts it in place (returned, to be submitted on the event loop)."""
    from prat import ideas

    accept = action == "idea-accept"
    try:
        idea = ideas.decide(
            ctx.settings.home, str(body["idea"]), accept=accept, note=str(body.get("note") or "")
        )
        if not (accept and body.get("build")):
            return f"{idea.title}: {idea.status}", None
        card_id = ideas.put_in_place(
            ctx.db, ctx.settings.home, idea.id, project=str(body.get("project") or "") or None
        )
    except ideas.IdeaError as exc:
        raise ValueError(str(exc)) from exc
    return f"{idea.title}: accepted; card {card_id} puts it in place and is running", card_id


async def _act(ctx: Any, action: str, body: dict[str, Any]) -> str:
    settings, db, engine = ctx.settings, ctx.db, ctx.engine
    if action == "approve":
        card_id = int(body["card_id"])
        merged = await engine.approve(card_id)
        return f"#{card_id} merged" + (f" as {merged.commit[:10]}" if merged.commit else "")
    if action == "rework":
        card_id = int(body["card_id"])
        feedback = str(body.get("feedback") or "").strip()
        if not feedback:
            raise ValueError("say what to change")
        # A card in review, blocked (this is the answer to its question) or failed.
        engine.rework(card_id, feedback)
        if not engine.is_scheduled(card_id):
            engine.submit(card_id)
        return f"#{card_id} sent back with your words; its agent is on it"
    if action == "job-start":
        run_id = await asyncio.to_thread(
            jobs.start, settings, db, str(body["project"]), str(body["name"])
        )
        return f"job {body['name']} started as run {run_id}"
    if action == "job-stop":
        await asyncio.to_thread(jobs.stop, db, int(body["run_id"]))
        return f"job run {body['run_id']} stopped"
    if action == "job-land":
        merged = await asyncio.to_thread(jobs.land, settings, db, int(body["run_id"]))
        return f"landed: {merged}"
    if action == "job-discard":
        await asyncio.to_thread(jobs.discard, settings, db, int(body["run_id"]))
        return f"job run {body['run_id']}'s data discarded"
    if action == "env-restart":
        from prat.environments import (
            EnvironmentManager,
            checkout_environment_variables,
            project_environments,
        )
        from prat.sandbox import environment_sandboxes

        slug, name = str(body["project"]), str(body["name"])
        checkout, envs, _problem = project_environments(db, settings.home, slug)
        env = next((e for e in envs if e.name == name), None)
        if env is None:
            raise LookupError(f"{slug} has no environment {name!r}")
        variables = checkout_environment_variables(db, settings.home, slug)
        manager = EnvironmentManager(settings.home, environment_sandboxes(settings))
        await asyncio.to_thread(manager.stop, slug, env, extra_env=variables)
        await asyncio.to_thread(
            manager.start_with_needs, slug, env, envs, checkout, extra_env=variables
        )
        return f"{slug} {name} restarted"
    if action in ("talk", "standup", "introduce"):
        from prat import converse, identity
        from prat.model_registry import Role, resolve_model

        model = resolve_model(Role.CREW, settings_models=settings.models)
        if action == "talk":
            message = str(body.get("message") or "").strip()
            if not message:
                raise ValueError("say something")
            return await converse.talk(
                settings.home, db, engine.runtime, str(body["agent"]), message, model=model
            )
        if action == "introduce":
            who = await identity.introduce(
                settings.home, engine.runtime, str(body["agent"]), model=model
            )
            return f"{who.name}: {who.bio}"
        reports = await converse.standup(settings.home, db, engine.runtime, model=model)
        if not reports:
            return "Nobody has worked in the last day."
        return "\n\n".join(f"{r.name}\n{r.text}" for r in reports)
    if action in ("social", "social-hello"):
        from prat import social
        from prat.model_registry import Role, resolve_model

        model = resolve_model(Role.CREW, settings_models=settings.models)
        if action == "social-hello":
            greeted = await social.say_hello(
                settings.home, engine.runtime, str(body.get("message") or ""), model=model
            )
            said = [t for t in greeted.turns if t.agent != social.PERSON][-social.GREETED :]
            return "\n\n".join(f"{t.name}: {t.text}" for t in said)
        met = await social.gather(
            settings.home,
            engine.runtime,
            model=model,
            size=settings.social_size or 5,
            rounds=settings.social_rounds,
        )
        if met is None:
            return "Fewer than two agents have introduced themselves; nobody met."
        return met.transcript(settings.home)
    if action == "ideate":
        from prat import ideas
        from prat.model_registry import Role, resolve_model

        report = await ideas.ideate(
            settings.home,
            engine.runtime,
            model=resolve_model(Role.CREW, settings_models=settings.models),
        )
        count = sum(report.agents.values())
        asked = len(report.agents)
        return (
            f"Asked {asked} agent{'s' if asked != 1 else ''} with new work; "
            f"{count} idea{'s' if count != 1 else ''} proposed."
        )
    if action in ("idea-accept", "idea-decline"):
        text, work_card = await asyncio.to_thread(_decide_idea, ctx, action, body)
        if work_card is not None:
            ctx.engine.submit(work_card)
        return text
    if action == "inherit":
        from prat import inherit
        from prat.consolidate import consolidate
        from prat.model_registry import Role, resolve_model

        only = str(body.get("project") or "") or None
        found = await asyncio.to_thread(inherit.inherit, db, settings.home, project=only)
        beliefs = await consolidate(
            settings.home,
            engine.runtime,
            model=resolve_model(Role.LIBRARIAN, settings_models=settings.models),
        )
        formed = sum(int(a.get("beliefs") or 0) for a in beliefs.agents.values())
        return inherit.describe(settings.home, found) + f"\n{formed} beliefs formed."
    raise ValueError(f"unknown action {action!r}")
