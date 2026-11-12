"""State-changing routes (POST).

The security middleware has already checked the Origin, the content type,
the session and the CSRF token before any of these run. Every handler
validates its own input and changes state only through the store and the
engine, which enforce the card state machine.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from starlette.datastructures import FormData

from prat import onboard, store
from prat.domain import CardKind, CardStatus, CommentAuthor, Complexity, RunStatus
from prat.engine import ApproveError, EngineError, RunResult
from prat.environments import database_environ
from prat.git import GitError, MergeError
from prat.projects import (
    ProjectFileError,
    default_agent_path,
    load_spec,
    slugify,
    validate_slug,
)
from prat.setup_agent import add_setup_card, create_project_database, needs_setup
from prat.web.app import WebContext, current_session, render, web_context
from prat.web.sessions import COOKIE_NAME
from prat.workspace.checkpoints import CheckpointError, undo_card

log = logging.getLogger(__name__)
router = APIRouter()

MAX_TITLE = 200
MAX_TEXT = 20_000


def _flash(request: Request, message: str, tone: str = "info") -> None:
    session = current_session(request)
    if session is not None:
        session.flash(message, tone)


def _go(request: Request, url: str) -> Response:
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def _text(form: FormData, name: str) -> str:
    value = form.get(name)
    return value.strip() if isinstance(value, str) else ""


def _log_failure(task: asyncio.Task[RunResult]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("card run failed outside its steps", exc_info=exc)


def _submit(ctx: WebContext, card_id: int) -> None:
    task = ctx.engine.submit(card_id)
    task.add_done_callback(_log_failure)


@router.post("/logout")
async def logout(request: Request) -> Response:
    ctx = web_context(request)
    ctx.sessions.delete(request.cookies.get(COOKIE_NAME))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return response


# Cards -----------------------------------------------------------------------------------


@router.post("/cards")
async def card_create(request: Request) -> Response:
    ctx = web_context(request)
    form = await request.form()
    values = {
        "project": _text(form, "project"),
        "title": _text(form, "title"),
        "goal": _text(form, "goal"),
        "criteria": _text(form, "criteria"),
        "complexity": _text(form, "complexity") or str(Complexity.MEDIUM),
        "start": "1" if form.get("start") else "",
    }
    errors: dict[str, str] = {}
    if not values["title"]:
        errors["title"] = "Give the card a title."
    elif len(values["title"]) > MAX_TITLE:
        errors["title"] = f"Keep the title under {MAX_TITLE} characters."
    for name in ("goal", "criteria"):
        if len(values[name]) > MAX_TEXT:
            errors[name] = f"Keep this under {MAX_TEXT} characters."
    if values["complexity"] not in {str(c) for c in Complexity}:
        errors["complexity"] = "Choose low, medium or high."
    card_id = None
    with ctx.db.session() as session:
        projects = store.list_projects(session)
        project = None
        if not values["project"]:
            errors["project"] = "Choose a project."
        else:
            try:
                project = store.get_project(session, values["project"])
            except store.NotFound:
                errors["project"] = "That project does not exist."
        if not errors and project is not None:
            card = store.add_card(
                session,
                project,
                title=values["title"],
                goal=values["goal"],
                acceptance_criteria=values["criteria"],
                complexity=Complexity(values["complexity"]),
                ready=bool(values["start"]),
            )
            card_id = card.id
    if card_id is None:
        return render(
            request,
            "card_new.html",
            {
                "projects": projects,
                "form": values,
                "errors": errors,
                "complexities": [str(c) for c in Complexity],
            },
            status_code=422,
            nav="board",
        )
    if values["start"]:
        _submit(ctx, card_id)
        _flash(request, "Card created and queued to run.", "ok")
    else:
        _flash(request, "Card created as a draft.", "ok")
    return _go(request, f"/cards/{card_id}")


def _card_or_404(session: Any, card_id: int) -> Any:
    try:
        return store.get_card(session, card_id)
    except store.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


def _queue(request: Request, card_id: int, *, allowed: tuple[CardStatus, ...]) -> Response:
    ctx = web_context(request)
    if ctx.engine.is_scheduled(card_id):
        _flash(request, "This card is already queued or running.", "warn")
        return _go(request, f"/cards/{card_id}")
    with ctx.db.session() as session:
        card = _card_or_404(session, card_id)
        if card.status not in allowed:
            _flash(request, f"A {card.status} card cannot be started.", "warn")
            return _go(request, f"/cards/{card_id}")
        if card.status in (CardStatus.DRAFT, CardStatus.BLOCKED, CardStatus.FAILED):
            store.set_card_status(session, card, CardStatus.READY, "started from the web UI")
    _submit(ctx, card_id)
    _flash(request, "Queued. The run starts as soon as a slot is free.", "ok")
    return _go(request, f"/cards/{card_id}")


@router.post("/cards/{card_id}/ready")
async def card_ready(request: Request, card_id: int) -> Response:
    return _queue(request, card_id, allowed=(CardStatus.DRAFT,))


@router.post("/cards/{card_id}/run")
async def card_run(request: Request, card_id: int) -> Response:
    return _queue(
        request,
        card_id,
        allowed=(
            CardStatus.DRAFT,
            CardStatus.READY,
            CardStatus.REWORK,
            CardStatus.BLOCKED,
            CardStatus.FAILED,
        ),
    )


@router.post("/cards/{card_id}/cancel")
async def card_cancel(request: Request, card_id: int) -> Response:
    ctx = web_context(request)
    with ctx.db.session() as session:
        _card_or_404(session, card_id)
    changed = await ctx.engine.cancel(card_id)
    _flash(request, "Cancelled." if changed else "Nothing to cancel.", "ok" if changed else "warn")
    return _go(request, f"/cards/{card_id}")


@router.post("/cards/{card_id}/rework")
async def card_rework(request: Request, card_id: int) -> Response:
    ctx = web_context(request)
    form = await request.form()
    feedback = _text(form, "feedback")
    with ctx.db.session() as session:
        card = _card_or_404(session, card_id)
        status = card.status
    if not feedback:
        _flash(request, "Say what should change before sending the card back.", "warn")
        return _go(request, f"/cards/{card_id}#rework")
    if len(feedback) > MAX_TEXT:
        _flash(request, f"Keep the feedback under {MAX_TEXT} characters.", "warn")
        return _go(request, f"/cards/{card_id}#rework")
    if status not in (CardStatus.REVIEW, CardStatus.BLOCKED, CardStatus.FAILED):
        _flash(request, f"A {status} card cannot be sent back for rework.", "warn")
        return _go(request, f"/cards/{card_id}")
    try:
        ctx.engine.rework(card_id, feedback)
    except (EngineError, ValueError) as exc:
        _flash(request, str(exc), "bad")
        return _go(request, f"/cards/{card_id}")
    if form.get("run_now"):
        _submit(ctx, card_id)
        _flash(request, "Sent back for rework and queued to run.", "ok")
    else:
        _flash(request, "Sent back for rework. Run it when you are ready.", "ok")
    return _go(request, f"/cards/{card_id}")


@router.post("/cards/{card_id}/approve")
async def card_approve(request: Request, card_id: int) -> Response:
    ctx = web_context(request)
    with ctx.db.session() as session:
        card = _card_or_404(session, card_id)
        agent_path, default = card.project.agent_path, card.project.default_branch
        kind, slug = CardKind(card.kind), card.project.slug
    try:
        merged = await ctx.engine.approve(card_id)
    except (ApproveError, MergeError, GitError) as exc:
        _flash(request, f"Not merged: {exc}", "bad")
        return _go(request, f"/cards/{card_id}")
    message = (
        f"Merged into {default} as {merged.commit[:10]} in the agent clone. "
        f"To bring it into your checkout: git pull {agent_path} {default}"
    )
    if kind is CardKind.SETUP:
        # The project file has landed: learn the project now, so the first real card is
        # briefed.
        from prat.web.routes_knowledge import start_deep_scan

        if start_deep_scan(request, slug):
            message += ". PRAT is now scanning the project to seed its knowledge."
    _flash(request, message, "ok")
    return _go(request, f"/cards/{card_id}")


@router.post("/cards/{card_id}/undo")
async def card_undo(request: Request, card_id: int) -> Response:
    """Reverse a landed card's code and data, keeping everything that landed since."""
    ctx = web_context(request)
    with ctx.db.session() as session:
        card = _card_or_404(session, card_id)
        agent_path, default = card.project.agent_path, card.project.default_branch
        slug = card.project.slug
    try:
        result = await asyncio.to_thread(
            functools.partial(
                undo_card,
                ctx.db,
                card_id,
                actor="web",
                environ=database_environ(ctx.settings.home, slug),
            )
        )
    except CheckpointError as exc:
        _flash(request, f"Not undone: {exc}", "bad")
        return _go(request, f"/cards/{card_id}")
    data_note = " and its data" if result.databases else ""
    _flash(
        request,
        f"Card #{card_id}'s code{data_note} reversed as {result.git_revert[:10]} in the agent "
        f"clone. To bring it into your checkout: git pull {agent_path} {default}",
        "ok",
    )
    return _go(request, f"/cards/{card_id}")


@router.post("/cards/{card_id}/comments")
async def card_comment(request: Request, card_id: int) -> Response:
    ctx = web_context(request)
    form = await request.form()
    body = _text(form, "body")
    if not body:
        _flash(request, "The comment is empty.", "warn")
        return _go(request, f"/cards/{card_id}#comments")
    if len(body) > MAX_TEXT:
        _flash(request, f"Keep comments under {MAX_TEXT} characters.", "warn")
        return _go(request, f"/cards/{card_id}#comments")
    with ctx.db.session() as session:
        card = _card_or_404(session, card_id)
        store.add_comment(session, card, body, author=CommentAuthor.HUMAN, kind="comment")
    return _go(request, f"/cards/{card_id}#comments")


@router.post("/cards/{card_id}/resume")
async def card_resume(request: Request, card_id: int) -> Response:
    ctx = web_context(request)
    if ctx.engine.is_scheduled(card_id):
        _flash(request, "This card is already running.", "warn")
        return _go(request, f"/cards/{card_id}")
    ctx.engine.recover()
    with ctx.db.session() as session:
        card = _card_or_404(session, card_id)
        run = store.latest_run(session, card)
        run_id = run.id if run is not None and run.status == RunStatus.INTERRUPTED else None
    if run_id is None:
        _flash(request, "There is no interrupted run to resume.", "warn")
        return _go(request, f"/cards/{card_id}")
    task = ctx.engine.submit_resume(card_id, run_id)
    task.add_done_callback(_log_failure)
    _flash(request, "Resuming the interrupted run.", "ok")
    return _go(request, f"/cards/{card_id}")


@router.post("/cards/{card_id}/reopen")
async def card_reopen(request: Request, card_id: int) -> Response:
    ctx = web_context(request)
    with ctx.db.session() as session:
        card = _card_or_404(session, card_id)
        if card.status != CardStatus.CANCELLED:
            _flash(request, "Only cancelled cards can be reopened.", "warn")
            return _go(request, f"/cards/{card_id}")
        store.set_card_status(session, card, CardStatus.DRAFT, "reopened from the web UI")
    _flash(request, "Reopened as a draft.", "ok")
    return _go(request, f"/cards/{card_id}")


# Projects -------------------------------------------------------------------------------


def _check_project(values: dict[str, str]) -> tuple[dict[str, str], dict[str, Any]]:
    """Validate a new project's paths and project file (runs in a worker thread)."""
    errors: dict[str, str] = {}
    found: dict[str, Any] = {}
    raw = values["checkout"]
    if not raw:
        errors["checkout"] = "Enter the path of your own clone of the repository."
        return errors, found
    checkout = Path(raw).expanduser()
    if not checkout.is_absolute():
        errors["checkout"] = "Use an absolute path."
        return errors, found
    checkout = checkout.resolve()
    if not checkout.is_dir():
        errors["checkout"] = f"{checkout} does not exist or is not a directory."
        return errors, found
    if not (checkout / ".git").exists():
        errors["checkout"] = f"{checkout} is not a git checkout (no .git found)."
        return errors, found
    try:
        spec = load_spec(checkout)
    except ProjectFileError as exc:
        errors["checkout"] = f"The project file is not valid: {exc}"
        return errors, found
    slug = values["slug"] or slugify(spec.name, 63)
    try:
        validate_slug(slug)
    except ValueError as exc:
        errors["slug"] = str(exc)
    agent = Path(values["agent_path"]).expanduser() if values["agent_path"] else None
    if agent is not None and not agent.is_absolute():
        errors["agent_path"] = "Use an absolute path."
    found.update(
        checkout=checkout,
        spec=spec,
        slug=slug,
        agent=agent.resolve() if agent is not None else default_agent_path(checkout),
    )
    return errors, found


@router.get("/project-branches")
async def project_branches(request: Request) -> Response:
    """The branch picker for a repository link (an htmx fragment)."""
    link = (request.query_params.get("checkout") or "").strip()
    context: dict[str, Any] = {"link": link, "branches": [], "default": None, "error": None}
    if onboard.is_link(link):
        try:
            found = await asyncio.to_thread(onboard.list_branches, link)
        except onboard.OnboardError as exc:
            context["error"] = str(exc)
        else:
            context.update(branches=found.names, default=found.default)
    return render(request, "_branch_field.html", context)


def _clone_link(ctx: WebContext, values: dict[str, str], errors: dict[str, str]) -> None:
    """Clone a repository link into the projects folder and point ``checkout`` at the clone
    (runs in a worker thread). Problems go to ``errors``."""
    link = values["checkout"]
    try:
        found = onboard.list_branches(link)
        branch = values["branch"] or found.default or found.names[0]
        if branch not in found.names:
            errors["branch"] = f"The repository has no branch {branch!r}."
            return
        slug = values["slug"] or onboard.slug_for(link)
        validate_slug(slug)
        destination = onboard.projects_dir(ctx.settings) / slug
        onboard.clone(link, branch, destination)
    except onboard.OnboardError as exc:
        errors["checkout"] = str(exc)
        return
    except ValueError as exc:
        errors["slug"] = str(exc)
        return
    values.update(checkout=str(destination), slug=slug, branch=branch)


@router.post("/projects/{slug}/setup")
async def project_setup(request: Request, slug: str) -> Response:
    """Start the setup agent on a registered project that has no project file yet."""
    ctx = web_context(request)
    try:
        await asyncio.to_thread(create_project_database, ctx.settings, slug)
    except Exception as exc:  # the project still gets set up, without one
        log.warning("no project database for %s: %s", slug, exc)
        _flash(request, f"No database was made for the project: {exc}", "warn")
    with ctx.db.session() as session:
        try:
            project = store.get_project(session, slug)
        except store.NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        card_id = add_setup_card(session, project, ctx.settings, database=False).id
    _submit(ctx, card_id)
    _flash(request, "The setup agent is working out how to install, test and run it.", "ok")
    return _go(request, f"/cards/{card_id}")


@router.post("/projects")
async def project_create(request: Request) -> Response:
    ctx = web_context(request)
    form = await request.form()
    values = {
        "checkout": _text(form, "checkout"),
        "slug": _text(form, "slug"),
        "agent_path": _text(form, "agent_path"),
        "branch": _text(form, "branch"),
    }
    wants_setup = form.get("setup") == "on"
    errors: dict[str, str] = {}
    link = values["checkout"] if onboard.is_link(values["checkout"]) else None
    if link is not None:
        await asyncio.to_thread(_clone_link, ctx, values, errors)
    found: dict[str, Any] = {}
    if not errors:
        errors, found = await asyncio.to_thread(_check_project, values)
    slug = None
    setup_card: int | None = None
    if not errors:
        spec = found["spec"]
        if link is not None and spec.default_branch != values["branch"]:
            spec = spec.model_copy(update={"default_branch": values["branch"]})
        with ctx.db.session() as session:
            try:
                project = store.add_project(
                    session,
                    slug=found["slug"],
                    checkout_path=found["checkout"],
                    agent_path=found["agent"],
                    spec=spec,
                )
                slug = project.slug
                if wants_setup and needs_setup(found["checkout"]):
                    try:
                        await asyncio.to_thread(create_project_database, ctx.settings, project.slug)
                    except Exception as exc:  # the project still gets set up, without one
                        log.warning("no project database for %s: %s", project.slug, exc)
                        _flash(request, f"No database was made for the project: {exc}", "warn")
                    card = add_setup_card(session, project, ctx.settings, database=False)
                    setup_card = card.id
            except PermissionError as exc:
                errors["agent_path"] = str(exc)
            except ValueError as exc:
                field = "slug" if "already exists" in str(exc) else "checkout"
                errors[field] = str(exc)
            if errors:
                session.rollback()
                slug = None
    if slug is None:
        if link is not None:
            values["checkout"] = link
        return render(
            request,
            "project_new.html",
            {"form": values, "errors": errors, "setup": wants_setup},
            status_code=422,
            nav="projects",
        )
    if setup_card is not None:
        _submit(ctx, setup_card)
        _flash(
            request,
            f"Project {slug} added. The setup agent is working out how to install, test and run "
            "it; review its proposal when the card reaches review.",
            "ok",
        )
        return _go(request, f"/cards/{setup_card}")
    _flash(request, f"Project {slug} added. PRAT makes its own clone on the first run.", "ok")
    return _go(request, f"/projects/{slug}")
