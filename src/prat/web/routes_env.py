"""Environments in the web UI: a project's own, and previews of a card in review."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from prat import store
from prat.domain import CardStatus
from prat.environments import (
    EnvironmentError,
    EnvironmentManager,
    EnvironmentView,
    checkout_environment_variables,
    database_environ,
    load_local,
    merged,
    prepare_preview,
    preview_path,
    project_environments,
    remove_preview,
    set_up_checkout,
)
from prat.infra import InfraReport, is_checking, last_report, run_check
from prat.projects import Environment, EnvironmentLink, ProjectSpec
from prat.web.app import render, web_context
from prat.web.routes_actions import _flash, _go, _submit
from prat.workspace.dolt_branch import WorkspaceError, providers_for

log = logging.getLogger(__name__)
router = APIRouter()
# Environment checks in flight; held so they are not collected before they finish.
_CHECKS: set[asyncio.Task[None]] = set()
ACTIONS = ("start", "stop", "restart")


@dataclass
class EnvironmentsPanel:
    slug: str
    views: list[EnvironmentView] = field(default_factory=list)
    problem: str | None = None
    card_id: int | None = None
    base: str = ""
    # Quick links from this machine's environments file (prat.environments.load_local).
    links: list[EnvironmentLink] = field(default_factory=list)
    # The project's last environment check, and whether one is running (prat.infra).
    check: InfraReport | None = None
    checking: bool = False


def _manager(request: Request) -> EnvironmentManager:
    manager = web_context(request).environments
    if manager is None:  # pragma: no cover - always set by create_app
        raise HTTPException(503, "environments are not available")
    return manager


def _project(request: Request, slug: str) -> tuple[Path, list[Environment], str | None]:
    ctx = web_context(request)
    try:
        return project_environments(ctx.db, ctx.settings.home, slug)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@dataclass
class CardPreview:
    slug: str
    agent_path: Path
    branch: str | None
    envs: list[Environment]
    variables: dict[str, str]
    problem: str | None
    spec: ProjectSpec | None = None


def card_environments(request: Request, card_id: int) -> CardPreview:
    """The environments a card's run recorded, where its work lives, and the variables that
    point a preview at the card's database branch."""
    ctx = web_context(request)
    with ctx.db.session() as session:
        try:
            card = store.get_card(session, card_id)
        except store.NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        slug, agent, branch = card.project.slug, Path(card.project.agent_path), card.branch
        run = store.latest_run(session, card)
        spec = ProjectSpec.model_validate(run.spec) if run and run.spec else None
    if spec is None or not branch:
        return CardPreview(slug, agent, None, [], {}, "the card has no work to preview yet")
    local = load_local(ctx.settings.home, slug)
    problem = local.problem
    envs = [env for env in merged(list(spec.environment), local) if env.preview]
    variables: dict[str, str] = {}
    try:
        environ = database_environ(ctx.settings.home, slug)
        for provider in providers_for(spec.workspace.database, environ=environ):
            variables.update(provider.agent_env(card_id))
    except WorkspaceError as exc:
        problem = f"the card's database branch is not available: {exc}"
    return CardPreview(slug, agent, branch, envs, variables, problem, spec)


def _find(envs: list[Environment], name: str) -> Environment:
    for env in envs:
        if env.name == name:
            return env
    raise HTTPException(404, f"no environment {name!r}")


@router.get("/projects/{slug}/environments")
async def project_panel(request: Request, slug: str) -> Response:
    manager = _manager(request)

    def load() -> EnvironmentsPanel:
        checkout, envs, problem = _project(request, slug)
        views = [manager.view(slug, env) for env in envs]
        home = web_context(request).settings.home
        return EnvironmentsPanel(
            slug,
            views,
            problem,
            base=str(checkout),
            links=load_local(home, slug).links,
            check=last_report(home, slug),
            checking=is_checking(slug),
        )

    panel = await asyncio.to_thread(load)
    return render(request, "_environments.html", {"panel": panel}, nav="projects")


@router.post("/projects/{slug}/environments/check")
async def project_check(request: Request, slug: str) -> Response:
    """Start an environment check in the background; the panel shows it running and then its
    report. When something failed, the infra card it filed is queued to run."""
    ctx = web_context(request)
    manager = _manager(request)
    _project(request, slug)  # 404 for an unknown project
    if is_checking(slug):
        _flash(request, "An environment check is already running.", "bad")
        return _go(request, f"/projects/{slug}#environments")

    async def check() -> None:
        try:
            report = await asyncio.to_thread(run_check, ctx.settings, ctx.db, manager, slug)
        except Exception:
            log.exception("environment check of %s failed", slug)
            return
        card_id = report.card_id
        if card_id is None or ctx.engine.is_scheduled(card_id):
            return
        with ctx.db.session() as session:
            ready = store.get_card(session, card_id).status is CardStatus.READY
        if ready:
            _submit(ctx, card_id)

    task = asyncio.create_task(check())
    _CHECKS.add(task)
    task.add_done_callback(_CHECKS.discard)
    _flash(request, "Checking every environment from a fresh checkout.", "ok")
    return _go(request, f"/projects/{slug}#environments")


@router.post("/projects/{slug}/environments/{name}/{action}")
async def project_action(request: Request, slug: str, name: str, action: str) -> Response:
    if action not in ACTIONS:
        raise HTTPException(404, "unknown action")
    manager = _manager(request)

    def act() -> str:
        checkout, envs, _ = _project(request, slug)
        env = _find(envs, name)
        if action == "stop":
            manager.stop(slug, env)
            return f"{env.title} stopped."
        ctx = web_context(request)
        variables = checkout_environment_variables(ctx.db, ctx.settings.home, slug)
        if action == "restart":
            manager.stop(slug, env, extra_env=variables)
            manager.start_with_needs(slug, env, envs, checkout, extra_env=variables)
            return f"{env.title} restarted."
        manager.start_with_needs(slug, env, envs, checkout, extra_env=variables)
        return f"{env.title} is starting."

    try:
        _flash(request, await asyncio.to_thread(act), "ok")
    except EnvironmentError as exc:
        _flash(request, str(exc), "bad")
    return _go(request, f"/projects/{slug}#environments")


@router.get("/cards/{card_id}/preview")
async def card_panel(request: Request, card_id: int) -> Response:
    manager = _manager(request)

    def load() -> EnvironmentsPanel:
        found = card_environments(request, card_id)
        views = [manager.view(found.slug, env, card_id=card_id) for env in found.envs]
        return EnvironmentsPanel(found.slug, views, found.problem, card_id)

    panel = await asyncio.to_thread(load)
    return render(request, "_environments.html", {"panel": panel}, nav="board")


@router.post("/cards/{card_id}/preview/{name}/{action}")
async def card_action(request: Request, card_id: int, name: str, action: str) -> Response:
    if action not in ACTIONS:
        raise HTTPException(404, "unknown action")
    manager = _manager(request)

    def act() -> str:
        found = card_environments(request, card_id)
        env = _find(found.envs, name)
        home = web_context(request).settings.home
        # One checkout per card, shared by the card's preview environments.
        checkout = preview_path(home, found.slug, card_id)

        def others_running() -> bool:
            return any(
                card == card_id and other != env.name
                for other, card in manager.running_previews(found.slug)
            )

        if action in ("stop", "restart"):
            manager.stop(found.slug, env, card_id=card_id, extra_env=found.variables)
            if action == "stop":
                if not others_running():
                    remove_preview(found.agent_path, checkout)
                return f"Preview of {env.title} stopped."
        if found.branch is None:
            raise EnvironmentError(found.problem or "nothing to preview")
        if not others_running() or not checkout.is_dir():
            # Nothing else uses the checkout: start from the tip of the card's branch, with the
            # dependencies the project's setup command installs.
            prepare_preview(found.agent_path, found.branch, checkout)
            sandbox = (
                manager.sandboxes(found.slug, card_id, checkout, found.variables)
                if manager.sandboxes
                else None
            )
            problem = set_up_checkout(found.spec, checkout, found.variables, sandbox=sandbox)
            if problem is not None:
                raise EnvironmentError(f"the preview could not be set up: {problem}")
        manager.start_with_needs(
            found.slug, env, found.envs, checkout, card_id=card_id, extra_env=found.variables
        )
        return f"Preview of {env.title} is starting on its own port."

    try:
        _flash(request, await asyncio.to_thread(act), "ok")
    except EnvironmentError as exc:
        _flash(request, str(exc), "bad")
    return _go(request, f"/cards/{card_id}#preview")
