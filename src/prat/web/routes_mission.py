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
    raise ValueError(f"unknown action {action!r}")
