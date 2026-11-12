"""The FastAPI application for PRAT's web UI.

The engine runs in-process on the server's event loop, so runs started from
the UI use the same queue and concurrency limits as everything else. Pages
are server-rendered with Jinja2 (autoescape always on); htmx and a small
script add live updates through server-sent events. No inline scripts or
styles exist anywhere, so the Content Security Policy can forbid them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.templating import Jinja2Templates

from prat import __version__, rhythm
from prat.config import Settings
from prat.db import Database
from prat.dream import DreamScheduler
from prat.engine import Engine
from prat.environments import EnvironmentManager
from prat.errors import record_error
from prat.improve.scheduler import ImproveScheduler
from prat.runtime.auth import AuthStatus, check_auth
from prat.web import views
from prat.web.icons import LOGO, icon
from prat.web.security import SecurityMiddleware, WebSecurity, security_config
from prat.web.sessions import LoginThrottle, SessionStore, WebSession
from prat.web.tokens import fingerprint, read_access_token

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
TEMPLATES_DIR = HERE / "templates"
STATIC_DIR = HERE / "static"
AUTH_CACHE_SECONDS = 30.0
# Activity is counted per hour, so one signal a minute is plenty.
SIGNAL_SECONDS = 60.0


@dataclass
class WebContext:
    settings: Settings
    db: Database
    engine: Engine
    runtime_name: str
    sessions: SessionStore
    security: WebSecurity
    templates: Jinja2Templates
    throttle: LoginThrottle = field(default_factory=LoginThrottle)
    auth_checker: Callable[[Settings], AuthStatus] = check_auth
    improve: ImproveScheduler | None = None
    environments: EnvironmentManager | None = None
    dreams: DreamScheduler | None = None
    _last_signal: float = 0.0
    _auth: tuple[float, AuthStatus] | None = None
    _token: tuple[int, str | None] | None = None

    def token_fingerprint(self) -> str | None:
        """Fingerprint of the current access token (re-read when the file changes)."""
        try:
            stamp = os.stat(self.settings.access_token_file).st_mtime_ns
        except OSError:
            self._token = None
            return None
        if self._token is None or self._token[0] != stamp:
            token = read_access_token(self.settings)
            self._token = (stamp, fingerprint(token) if token else None)
        return self._token[1]

    async def auth_status(self) -> AuthStatus:
        cached = self._auth
        if cached is not None and time.monotonic() - cached[0] < AUTH_CACHE_SECONDS:
            return cached[1]
        status = await asyncio.to_thread(self.auth_checker, self.settings)
        self._auth = (time.monotonic(), status)
        return status

    async def signal(self) -> None:
        """Note that a person is using PRAT (see prat.rhythm); at most once a minute."""
        now = time.monotonic()
        if self._last_signal and now - self._last_signal < SIGNAL_SECONDS:
            return
        self._last_signal = now
        try:
            await asyncio.to_thread(rhythm.record_signal, self.db)
        except Exception:
            log.exception("could not record activity")


def _is_person(request: Request) -> bool:
    """A page a person opened or an action they took; not polling, streams or files."""
    path = request.url.path
    if path.startswith(("/static", "/stream", "/events")) or path.endswith("/stream"):
        return False
    if request.method != "GET":
        return True
    # htmx refreshes and polls carry HX-Request; a full page load does not.
    return request.headers.get("hx-request") != "true"


def make_templates() -> Jinja2Templates:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(
        icon=icon,
        logo=LOGO,
        version=__version__,
        status_label=views.status_label,
        status_tone=views.status_tone,
        status_help=views.STATUS_HELP,
        verdict_label=views.VERDICT_LABEL,
        verdict_tone=views.VERDICT_TONE,
        category_label=views.CATEGORY_LABEL,
        agent_label=views.AGENT_LABEL,
    )
    env.filters.update(
        usd=views.fmt_usd,
        duration=views.fmt_duration,
        filesize=views.fmt_bytes,
        ago=views.fmt_ago,
        timestamp=views.fmt_time,
        isotime=lambda value: v.isoformat() if (v := views.utc(value)) else "",
    )
    return Jinja2Templates(env=env)


def web_context(request: Request) -> WebContext:
    context: WebContext = request.app.state.prat
    return context


def current_session(request: Request) -> WebSession | None:
    session = request.scope.get("state", {}).get("session")
    return session if isinstance(session, WebSession) else None


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    nav: str = "",
) -> Response:
    ctx = web_context(request)
    session = current_session(request)
    data: dict[str, Any] = {
        "request": request,
        "nav": nav,
        "signed_in": session is not None,
        "csrf_token": session.csrf_token if session else "",
        # Fragments (templates named _*) refresh parts of a page and never show notices,
        # so they must not use them up before the full page does.
        "flashes": session.pop_flashes() if session and not name.startswith("_") else [],
        "runtime_name": ctx.runtime_name,
    }
    data.update(context or {})
    return ctx.templates.TemplateResponse(request, name, data, status_code=status_code)


def create_app(
    settings: Settings,
    db: Database,
    engine: Engine,
    *,
    runtime_name: str,
    host: str | None = None,
    port: int | None = None,
    auth_checker: Callable[[Settings], AuthStatus] = check_auth,
    improve: bool = True,
    dreams: bool = True,
) -> FastAPI:
    """Build the application. The engine must not be running anything yet.

    ``improve`` starts the self-improvement scheduler with the server. It does
    nothing until self-improvement is turned on. ``dreams`` starts the quiet-hours
    scheduler (prat.dream), which works only in the learned dream window.
    """
    security = security_config(
        host or settings.web_host, port or settings.web_port, settings.web_public_url
    )
    sessions = SessionStore(db, settings.web_session_hours * 3600)
    context = WebContext(
        settings=settings,
        db=db,
        engine=engine,
        runtime_name=runtime_name,
        sessions=sessions,
        security=security,
        templates=make_templates(),
        auth_checker=auth_checker,
        improve=ImproveScheduler(db, settings, engine) if improve else None,
        environments=EnvironmentManager(settings.home),
        dreams=(
            DreamScheduler(db, settings, lambda: engine.runtime, runtime_name=runtime_name)
            if dreams
            else None
        ),
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        recovered = engine.recover()
        if recovered:
            log.warning("runs interrupted by an earlier stop: %s", recovered)
        # Runs a restart (or a crash) interrupted carry on, and cards that were queued queue again.
        picked = engine.resume_after_restart(recovered)
        if picked:
            log.warning("picked up again after the restart: cards %s", picked)
        if context.improve is not None:
            context.improve.start()
        if context.dreams is not None:
            context.dreams.start()
        try:
            yield
        finally:
            if context.dreams is not None:
                await context.dreams.stop()
            if context.improve is not None:
                await context.improve.stop()
            # Not cancel: that would end the runs and drop their database branches.
            await engine.suspend()

    app = FastAPI(
        title="PRAT",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.prat = context

    from prat.web.routes_actions import router as actions_router
    from prat.web.routes_env import router as env_router
    from prat.web.routes_improve import router as improve_router
    from prat.web.routes_knowledge import router as knowledge_router
    from prat.web.routes_pages import router as pages_router
    from prat.web.routes_rhythm import router as rhythm_router
    from prat.web.routes_stream import router as stream_router
    from prat.web.routes_wall import router as wall_router

    app.include_router(pages_router)
    app.include_router(actions_router)
    app.include_router(improve_router)
    app.include_router(stream_router)
    app.include_router(wall_router)
    app.include_router(env_router)
    app.include_router(knowledge_router)
    app.include_router(rhythm_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code in (404, 400, 409) and "text/html" in request.headers.get("accept", ""):
            return render(
                request,
                "error.html",
                {"status": exc.status_code, "message": str(exc.detail)},
                status_code=exc.status_code,
            )
        return await http_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def _server_error(request: Request, exc: Exception) -> Response:
        # The query string is left out: it can carry a sign-in code.
        await asyncio.to_thread(
            record_error,
            db,
            "web",
            type(exc).__name__,
            str(exc),
            detail={"method": request.method, "path": request.url.path},
            exc=exc,
        )
        return PlainTextResponse("Internal Server Error", status_code=500)

    @app.middleware("http")
    async def _activity(request: Request, call_next: Callable[[Request], Any]) -> Response:
        response: Response = await call_next(request)
        if current_session(request) is not None and _is_person(request):
            await context.signal()
        return response

    # Added last so it runs first: the session is known by the time _activity looks.
    app.add_middleware(
        SecurityMiddleware,
        config=security,
        sessions=sessions,
        token_fingerprint=context.token_fingerprint,
    )
    return app
