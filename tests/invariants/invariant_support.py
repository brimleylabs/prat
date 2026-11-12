"""Support for the invariant suite. Self-contained on purpose.

The invariant tests are the fixed contract of what must stay true whatever
else changes. When PRAT judges a change to itself, the copy of this folder from
before the change is run against the candidate, outside the candidate's tree.
So nothing here may import from the rest of the test suite (its conftest or
helpers), and every test is synchronous so it runs under a minimal pytest
configuration.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prat import store
from prat.config import AuthMode, Settings, load_settings
from prat.db import Database, Project
from prat.db.migrate import upgrade
from prat.domain import Autonomy
from prat.engine import Engine, FakeReviewer
from prat.projects import load_spec
from prat.runtime import FakeRuntime
from prat.runtime.auth import AuthStatus

PY = Path(sys.executable).as_posix()
BASE = "http://127.0.0.1:18300"
EVIL = "http://evil.example"
CSRF_META = re.compile(r'<meta name="csrf-token" content="([^"]*)">')

PROJECT_TOML = f"""
name = "inv"
default_branch = "main"
autonomy = "medium"
protected_paths = ["migrations/**"]

[commands]
test = ['{PY}', '-c', "import sys; sys.exit(0)"]

[verify]
gates = ["test"]
timeout_seconds = 60
"""

WRITE_FILE: list[dict[str, Any]] = [
    {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"},
    {"op": "finish", "message": "done"},
]


# The tests' own git commands ignore the machine's global and system git settings
# (hooks, signing, templates), so results do not depend on whose machine runs them.
# PRAT's own git calls still see them, as they would in real use.
_EMPTY_CONFIG = Path(tempfile.mkdtemp(prefix="prat-inv-git-")) / "gitconfig"
_EMPTY_CONFIG.write_text("", encoding="utf-8")
atexit.register(shutil.rmtree, _EMPTY_CONFIG.parent, True)
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": str(_EMPTY_CONFIG), "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        env=GIT_ENV,
    )
    return result.stdout.strip()


def try_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        env=GIT_ENV,
    )


def make_repo(root: Path, name: str = "inv") -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test Person")
    git(repo, "config", "user.email", "person@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "README.md").write_text("# inv\n", encoding="utf-8", newline="\n")
    (repo / ".prat").mkdir()
    (repo / ".prat" / "project.toml").write_text(PROJECT_TOML, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial commit")
    return repo


@dataclass
class Setup:
    settings: Settings
    db: Database
    repo: Path
    project: Project


def setup(tmp_path: Path) -> Setup:
    settings = load_settings(
        tmp_path / "home",
        stale_run_seconds=0,
        heartbeat_seconds=0.05,
        max_autonomy=Autonomy.HIGH,
    )
    settings.ensure_dirs()
    db = Database(settings.db_url)
    upgrade(db)
    repo = make_repo(tmp_path / "repos")
    with db.session() as session:
        project = store.add_project(
            session,
            slug="inv",
            checkout_path=repo,
            agent_path=repo.parent / "inv-agent",
            spec=load_spec(repo),
        )
        session.expunge(project)
    return Setup(settings, db, repo, project)


def engine_for(
    s: Setup, script: Any = None, reviewer: Any = None, security_reviewer: Any = None
) -> Engine:
    runtime = FakeRuntime(script if script is not None else WRITE_FILE)
    return Engine(
        s.settings,
        s.db,
        runtime,
        reviewer=reviewer or FakeReviewer(),
        security_reviewer=security_reviewer,
    )


def edit_project_file(s: Setup, old: str, new: str, message: str) -> None:
    """Change the committed project file (the copy the engine reads)."""
    toml = s.repo / ".prat" / "project.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    git(s.repo, "commit", "-q", "-am", message)


def add_card(s: Setup, title: str = "Add greet") -> int:
    with s.db.session() as session:
        project = store.get_project(session, s.project.id)
        return store.add_card(session, project, title=title, ready=True).id


def run_card(engine: Engine, card_id: int) -> Any:
    return asyncio.run(engine.run_card(card_id))


def usable_auth(_settings: Settings) -> AuthStatus:
    return AuthStatus(AuthMode.API_KEY, True, "test", "env")


@contextmanager
def web(s: Setup, *, login: bool = True) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from prat.web import create_app
    from prat.web.tokens import ensure_access_token

    app = create_app(
        s.settings,
        s.db,
        engine_for(s),
        runtime_name="fake",
        auth_checker=usable_auth,
        improve=False,
        dreams=False,
    )
    with TestClient(app, base_url=BASE) as client:
        client.csrf = ""  # type: ignore[attr-defined]
        if login:
            token = ensure_access_token(s.settings)
            response = client.post(
                "/login", data={"token": token}, headers={"Origin": BASE}, follow_redirects=False
            )
            assert response.status_code == 303, response.text
            match = CSRF_META.search(client.get("/board").text)
            assert match and match.group(1)
            client.csrf = match.group(1)  # type: ignore[attr-defined]
        yield app, client


def concrete(path: str) -> str:
    """A route path with its parameters filled in."""
    return re.sub(r"\{[^}]+\}", lambda m: "1" if "id" in m.group(0) else "inv", path)


def routes(app: Any) -> list[tuple[str, str]]:
    """Every (method, path) the application serves, except static files.

    Walks included routers whatever form the framework keeps them in. Callers
    assert a minimum count, so a change in that form fails closed rather than
    checking nothing.
    """
    found: list[tuple[str, str]] = []

    def walk(items: Any, prefix: str) -> None:
        for route in items:
            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            original = getattr(route, "original_router", None)
            if original is not None:
                context = getattr(route, "include_context", None)
                walk(original.routes, prefix + (getattr(context, "prefix", "") or ""))
            elif path is not None and methods:
                full = prefix + path
                if not full.startswith("/static"):
                    found.extend((m, full) for m in sorted(methods) if m != "HEAD")
            elif getattr(route, "routes", None) is not None:
                if path and path.startswith("/static"):
                    continue
                walk(route.routes, prefix + (path or ""))

    walk(app.routes, "")
    return sorted(set(found))
