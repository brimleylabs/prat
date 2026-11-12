"""Pages and HTML fragments (GET only, nothing here changes state)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.engine import make_url

from prat import __version__, project_detect, store
from prat.domain import CardStatus, Complexity
from prat.journal import read_journal
from prat.model_registry import DEFAULT_MODELS, Role, resolve_model
from prat.projects import (
    PROJECT_FILE_REL,
    ProjectFileError,
    command_text,
    parse_spec,
    project_file_at,
)
from prat.web import data, views
from prat.web.app import current_session, render, web_context
from prat.web.sessions import COOKIE_NAME
from prat.web.tokens import check_login_code, token_permissions_ok, tokens_equal

router = APIRouter()


def safe_next(target: str | None) -> str:
    """Only local paths are allowed as a post-login destination."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/board"
    if any(ch in target for ch in "\r\n"):
        return "/board"
    return target


def start_session(request: Request, target: str) -> Response:
    ctx = web_context(request)
    fingerprint = ctx.token_fingerprint()
    if fingerprint is None:  # pragma: no cover - checked by callers
        raise HTTPException(500, "no access token")
    old = request.cookies.get(COOKIE_NAME)
    ctx.sessions.delete(old)
    session = ctx.sessions.create(fingerprint)
    response = RedirectResponse(safe_next(target), status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        session.id,
        httponly=True,
        samesite="strict",
        secure=ctx.security.secure_cookies,
        path="/",
    )
    return response


@router.get("/")
async def index() -> Response:
    return RedirectResponse("/board", status_code=303)


@router.get("/favicon.ico")
async def favicon() -> Response:
    return RedirectResponse("/static/favicon.svg", status_code=301)


@router.get("/healthz")
async def healthz() -> Response:
    """Public liveness check: the version and commit, nothing else."""
    from prat.build import build_commit

    commit = await asyncio.to_thread(build_commit)
    return JSONResponse({"status": "ok", "version": __version__, "commit": commit})


@router.get("/login")
async def login_page(
    request: Request, code: str | None = None, next: str | None = None
) -> Response:
    ctx = web_context(request)
    error = None
    if current_session(request) is not None:
        # Already signed in: an old or reopened login link just goes to the page.
        return RedirectResponse(safe_next(next), status_code=303)
    if code is not None:
        from prat.web.tokens import read_access_token

        token = read_access_token(ctx.settings)
        if ctx.throttle.blocked():
            error = "Too many failed attempts. Wait a minute and try again."
        elif (
            token
            and (checked := check_login_code(token, code)) is not None
            and ctx.sessions.claim_login_nonce(*checked)
        ):
            ctx.throttle.reset()
            return start_session(request, next or "/board")
        else:
            ctx.throttle.fail()
            error = (
                "This login link has expired or was already used. "
                "Run prat login-url for a new one, or paste the access token."
            )
    return render(
        request,
        "login.html",
        {"error": error, "next": safe_next(next), "has_token": ctx.token_fingerprint() is not None},
        status_code=401 if error else 200,
    )


@router.post("/login")
async def login_submit(request: Request) -> Response:
    ctx = web_context(request)
    form = await request.form()
    given = str(form.get("token") or "").strip()
    target = safe_next(str(form.get("next") or "/board"))
    from prat.web.tokens import read_access_token

    token = read_access_token(ctx.settings)
    if ctx.throttle.blocked():
        return render(
            request,
            "login.html",
            {
                "error": "Too many failed attempts. Wait a minute and try again.",
                "next": target,
                "has_token": token is not None,
            },
            status_code=429,
        )
    if token and given and tokens_equal(given, token):
        ctx.throttle.reset()
        return start_session(request, target)
    ctx.throttle.fail()
    return render(
        request,
        "login.html",
        {"error": "That token is not right.", "next": target, "has_token": token is not None},
        status_code=401,
    )


# Board -----------------------------------------------------------------------------------


def _project_filter(session: Any, slug: str | None) -> Any:
    if not slug:
        return None
    try:
        return store.get_project(session, slug)
    except store.NotFound as exc:
        raise HTTPException(404, f"no project {slug!r}") from exc


@router.get("/board")
async def board(request: Request, project: str | None = None) -> Response:
    ctx = web_context(request)
    with ctx.db.session() as session:
        selected = _project_filter(session, project)
        columns = data.load_board(session, ctx.engine, selected)
        projects = store.list_projects(session)
        selected_slug = selected.slug if selected else ""
    auth = await ctx.auth_status()
    signed_in = auth.usable or ctx.runtime_name.startswith("fake")

    def setup() -> data.SetupView:
        with ctx.db.session() as session:
            return data.load_setup(session, signed_in=signed_in, sign_in_detail=auth.detail)

    checklist = await asyncio.to_thread(setup)
    return render(
        request,
        "board.html",
        {
            "columns": columns,
            "projects": projects,
            "project": selected_slug,
            "multi_project": not selected_slug,
            "total": sum(len(c.tiles) for c in columns),
            "setup": None if checklist.complete else checklist,
        },
        nav="board",
    )


@router.get("/board/columns")
async def board_columns(request: Request, project: str | None = None) -> Response:
    ctx = web_context(request)
    with ctx.db.session() as session:
        selected = _project_filter(session, project)
        columns = data.load_board(session, ctx.engine, selected)
        selected_slug = selected.slug if selected else ""
    return render(
        request,
        "_board_columns.html",
        {"columns": columns, "project": selected_slug, "multi_project": not selected_slug},
    )


# Cards ---------------------------------------------------------------------------------


@router.get("/cards/new")
async def card_new(request: Request, project: str | None = None) -> Response:
    ctx = web_context(request)
    with ctx.db.session() as session:
        projects = store.list_projects(session)
    return render(
        request,
        "card_new.html",
        {
            "projects": projects,
            "form": {"project": project or (projects[0].slug if len(projects) == 1 else "")},
            "errors": {},
            "complexities": [str(c) for c in Complexity],
        },
        nav="board",
    )


async def _card_context(request: Request, card_id: int, run: int | None) -> dict[str, Any]:
    ctx = web_context(request)
    with ctx.db.session() as session:
        try:
            page = data.load_card(session, ctx.engine, card_id, run)
        except store.NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        source = None
        if page.run is not None:
            run_row = next(r for r in page.card.runs if r.id == page.run.id)
            source = data.diff_source(page.card, run_row)
    if source is not None:
        page.diff = await asyncio.to_thread(data.load_diff, ctx.engine.worktrees, source)
    return {
        "page": page,
        "card": page.card,
        "selected_run": run,
    }


@router.get("/cards/{card_id}")
async def card_detail(request: Request, card_id: int, run: int | None = None) -> Response:
    context = await _card_context(request, card_id, run)
    return render(request, "card.html", context, nav="board")


@router.get("/cards/{card_id}/panel")
async def card_panel(request: Request, card_id: int, run: int | None = None) -> Response:
    context = await _card_context(request, card_id, run)
    return render(request, "_card_panel.html", context)


@router.get("/cards/{card_id}/runs/{run_id}/replay")
async def replay(
    request: Request,
    card_id: int,
    run_id: int,
    category: str | None = None,
    agent: str | None = None,
) -> Response:
    ctx = web_context(request)
    with ctx.db.session() as session:
        try:
            card = store.get_card(session, card_id)
        except store.NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        run = next((r for r in card.runs if r.id == run_id), None)
        if run is None:
            raise HTTPException(404, f"card {card_id} has no run {run_id}")
        run_info = data.run_view(run)
        title, project_slug = card.title, card.project.slug
    entries = list(read_journal(Path(run_info.journal_path))) if run_info.journal_path else []
    presented = views.present_all(entries)
    category = category if category in views.CATEGORY_LABEL else None
    agent = agent if agent in views.AGENT_LABEL else None
    shown = [
        v
        for v in presented
        if (category is None or v.category == category) and (agent is None or v.agent == agent)
    ]
    agents = sorted({v.agent for v in presented}, key=list(views.AGENT_LABEL).index)
    duration = views.seconds_between(entries[0].ts, entries[-1].ts) if len(entries) > 1 else None
    context = {
        "card_id": card_id,
        "card_title": title,
        "project_slug": project_slug,
        "run": run_info,
        "entries": shown,
        "total": len(presented),
        "histogram": views.tool_histogram(entries),
        "categories": views.category_counts(presented),
        "agents": agents,
        "category": category or "",
        "agent": agent or "",
        "duration": duration,
        "tool_calls": sum(1 for v in presented if v.category == "tool"),
        "denials": sum(1 for v in presented if v.category == "denied"),
    }
    template = "_replay_entries.html" if request.headers.get("hx-request") else "replay.html"
    return render(request, template, context, nav="board")


# Usage ------------------------------------------------------------------------------------


@router.get("/usage")
async def usage(request: Request, days: int = 30) -> Response:
    if days not in data.USAGE_PERIODS:
        days = 30
    ctx = web_context(request)
    with ctx.db.session() as session:
        page = data.load_usage(session, days, datetime.now(UTC))
    return render(request, "usage.html", {"page": page, "periods": data.USAGE_PERIODS}, nav="usage")


# Projects ---------------------------------------------------------------------------------


@router.get("/projects")
async def projects(request: Request) -> Response:
    ctx = web_context(request)
    with ctx.db.session() as session:
        rows = data.load_projects(session)
    return render(request, "projects.html", {"rows": rows}, nav="projects")


@router.get("/projects/new")
async def project_new(request: Request) -> Response:
    return render(request, "project_new.html", {"form": {}, "errors": {}}, nav="projects")


def _needs_project_file(checkout: Path) -> bool:
    return checkout.is_dir() and project_file_at(checkout) is None


@router.get("/projects/{slug}")
async def project_detail(request: Request, slug: str) -> Response:
    ctx = web_context(request)
    with ctx.db.session() as session:
        try:
            project = store.get_project(session, slug)
        except store.NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        cards = store.list_cards(session, project)
        recent = sorted(cards, key=lambda c: c.id, reverse=True)[:12]
        fallback = data.registered_spec(project)
        agent_path = Path(project.agent_path)
        checkout = Path(project.checkout_path)
        default_branch = project.default_branch
    committed = None
    error = None
    raw = None
    if await asyncio.to_thread(agent_path.exists):
        raw = await asyncio.to_thread(data.committed_project_file, agent_path, default_branch)
        if raw is not None:
            try:
                committed = parse_spec(raw, source=f"{default_branch}:{PROJECT_FILE_REL}")
            except ProjectFileError as exc:
                error = str(exc)
    suggestion = None
    if raw is None and await asyncio.to_thread(_needs_project_file, checkout):
        suggestion = await asyncio.to_thread(project_detect.suggest, checkout)
    spec = committed or fallback
    source = (
        f"{PROJECT_FILE_REL} on {default_branch} in the agent clone"
        if committed is not None
        else "the snapshot taken when the project was registered"
    )
    commands = {name: command_text(cmd) for name, cmd in spec.commands.defined().items()}
    return render(
        request,
        "project.html",
        {
            "project": project,
            "spec": spec,
            "commands": commands,
            "source": source,
            "spec_error": error,
            "raw": raw,
            "suggestion": suggestion,
            "recent": recent,
            "ceiling": str(ctx.settings.max_autonomy),
            "effective": str(
                min((spec.autonomy, ctx.settings.max_autonomy), key=lambda level: level.rank)
            ),
            "models": {
                str(role): resolve_model(
                    role, settings_models=ctx.settings.models, project_models=spec.models
                )
                for role in Role
            },
        },
        nav="projects",
    )


# Settings --------------------------------------------------------------------------------


def _database_label(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "configured (not shown)"


@router.get("/settings")
async def settings_page(request: Request) -> Response:
    ctx = web_context(request)
    settings = ctx.settings
    status = await ctx.auth_status()
    models = []
    for role in Role:
        configured = settings.models.get(str(role))
        models.append(
            {
                "role": str(role),
                "model": resolve_model(role, settings_models=settings.models),
                "source": "config.toml" if configured else "default",
                "default": DEFAULT_MODELS[role],
            }
        )
    engine = ctx.engine
    with ctx.db.session() as session:
        running = sum(1 for c in store.list_cards(session) if c.status == CardStatus.RUNNING)
    return render(
        request,
        "settings.html",
        {
            "auth": status,
            "models": models,
            "settings": settings,
            "running": running,
            "active": engine.limiter.running,
            "queued": len(engine.scheduled_cards()) - engine.limiter.running,
            # The store the server opened (a Dolt URL carries a password: hidden).
            "database": _database_label(ctx.db.url),
            "origins": sorted(ctx.security.allowed_origins),
            "token_file": str(settings.access_token_file),
            "token_private": token_permissions_ok(settings.access_token_file),
            "prat_version": __version__,
        },
        nav="settings",
    )
