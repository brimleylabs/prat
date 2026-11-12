"""Helpers for web UI tests: an app wired to the fake runtime and a signed-in client."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import WRITE_GREET
from prat import store
from prat.config import AuthMode, Settings
from prat.db import Database, Project
from prat.domain import CardStatus
from prat.engine import Engine, FakeReviewer
from prat.runtime import AgentTask, FakeRuntime, FakeScript
from prat.runtime.auth import AuthStatus
from prat.web import create_app
from prat.web.tokens import ensure_access_token

BASE = "http://127.0.0.1:18300"
ORIGIN = BASE
EVIL = "http://evil.example"

CSRF_META = re.compile(r'<meta name="csrf-token" content="([^"]*)">')


def usable_auth(_settings: Settings) -> AuthStatus:
    return AuthStatus(AuthMode.API_KEY, True, "API key found in environment variable", "env")


@dataclass
class Web:
    client: TestClient
    app: FastAPI
    settings: Settings
    db: Database
    engine: Engine
    runtime: FakeRuntime
    project: Project
    scripts: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    csrf: str = ""

    def login(self) -> None:
        token = ensure_access_token(self.settings)
        response = self.client.post(
            "/login", data={"token": token}, headers={"Origin": ORIGIN}, follow_redirects=False
        )
        assert response.status_code == 303, response.text
        self.refresh_csrf()

    def refresh_csrf(self) -> None:
        page = self.client.get("/board")
        match = CSRF_META.search(page.text)
        assert match and match.group(1), "no CSRF token on the page"
        self.csrf = match.group(1)

    def post(
        self,
        url: str,
        data: dict[str, str] | None = None,
        *,
        csrf: bool = True,
        origin: str | None = ORIGIN,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        sent = dict(headers or {})
        if origin is not None:
            sent["Origin"] = origin
        if csrf:
            sent["X-CSRF-Token"] = self.csrf
        return self.client.post(url, data=data or {}, headers=sent, follow_redirects=False)

    def status(self, card_id: int) -> CardStatus:
        with self.db.session() as session:
            return store.get_card(session, card_id).status

    def wait_for(self, card_id: int, *statuses: CardStatus, timeout: float = 30.0) -> CardStatus:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.status(card_id)
            if status in statuses and not self.engine.is_scheduled(card_id):
                return status
            time.sleep(0.05)
        raise AssertionError(f"card {card_id} is {self.status(card_id)}, wanted {statuses}")

    def add_card(self, title: str = "Add greet", **kwargs: Any) -> int:
        with self.db.session() as session:
            project = store.get_project(session, self.project.id)
            return store.add_card(session, project, title=title, **kwargs).id

    def card_count(self) -> int:
        with self.db.session() as session:
            return len(store.list_cards(session))


@contextmanager
def make_web(
    settings: Settings,
    db: Database,
    project: Project,
    *,
    reviewer: Any = None,
    auth_checker: Callable[[Settings], AuthStatus] = usable_auth,
    login: bool = True,
    **overrides: Any,
) -> Iterator[Web]:
    settings = settings.model_copy(
        update={"web_poll_seconds": 0.05, "web_stream_seconds": 10.0, **overrides}
    )
    scripts: dict[int, list[dict[str, Any]]] = {}

    def script_for(task: AgentTask) -> FakeScript:
        card_id = int(task.metadata.get("card_id", "0"))
        return FakeScript.parse(scripts.get(card_id, WRITE_GREET))

    runtime = FakeRuntime(script_for, state_dir=settings.home / "fake-sessions")
    engine = Engine(settings, db, runtime, reviewer=reviewer or FakeReviewer())
    # Dreams start on the clock (01:00 to 05:00 with no history); tests start them by hand.
    app = create_app(
        settings, db, engine, runtime_name="fake", auth_checker=auth_checker, dreams=False
    )
    with TestClient(app, base_url=BASE) as client:
        web = Web(client, app, settings, db, engine, runtime, project, scripts)
        if login:
            web.login()
        yield web
