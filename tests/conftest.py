from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from prat import store
from prat.config import Settings, load_settings
from prat.db import Database, Project
from prat.db.migrate import upgrade
from prat.domain import Autonomy
from prat.engine import Engine, FakeReviewer, FakeSecurityReviewer
from prat.projects import load_spec
from prat.runtime import FakeRuntime

# Read before the per-test isolation removes every PRAT_ variable. Example:
# PRAT_TEST_DOLT_URL=mysql+pymysql://root@127.0.0.1:3320 runs the suite on Dolt.
TEST_DOLT_URL = os.environ.get("PRAT_TEST_DOLT_URL", "").strip()


def dolt_admin(sql: str) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(TEST_DOLT_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(sql))
    finally:
        engine.dispose()


# PRAT_TEST_DOLTGRES_URL=postgresql+psycopg://postgres:password@127.0.0.1:5480 runs the Doltgres
# workspace tests; each makes its own database there and drops it afterwards.
TEST_DOLTGRES_URL = os.environ.get("PRAT_TEST_DOLTGRES_URL", "").strip().rstrip("/")


def doltgres_admin(sql: str) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(f"{TEST_DOLTGRES_URL}/postgres", isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text(sql))
    finally:
        engine.dispose()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Real agent runs cost money and need credentials: opt in with PRAT_E2E=1."""
    if os.environ.get("PRAT_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="real agent run; set PRAT_E2E=1 to enable")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def _empty_git_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("git") / "gitconfig"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, _empty_git_config: Path) -> None:
    for key in list(os.environ):
        if key.startswith("PRAT_"):
            monkeypatch.delenv(key, raising=False)
    # The tests' own git commands ignore the machine's global and system settings
    # (hooks, signing, templates). PRAT's git runner drops these two variables, so
    # PRAT itself still meets the machine's real settings, as it would in use.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(_empty_git_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # Tests use SQLite (or PRAT_TEST_DOLT_URL) unless they choose a store themselves:
    # a Dolt binary on the PATH must not start servers in the middle of the suite.
    monkeypatch.setenv("PRAT_STORE", "sqlite")


def make_dir_link(link: Path, target: Path) -> bool:
    """A symlink, or a directory junction on Windows without symlink rights."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0 and link.exists()
    return False


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, encoding="utf-8"
    )
    return result.stdout.strip()


def make_repo(
    root: Path,
    name: str = "acme-api",
    *,
    project_toml: str | None = None,
    files: dict[str, str] | None = None,
) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test Person")
    git(repo, "config", "user.email", "person@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8", newline="\n")
    for rel, content in (files or {}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    if project_toml is not None:
        (repo / ".prat").mkdir()
        (repo / ".prat" / "project.toml").write_text(project_toml, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial commit")
    return repo


PY = Path(sys.executable).as_posix()


def project_toml(
    *,
    test_code: str = "import sys; sys.exit(0)",
    autonomy: str = "medium",
    extra: str = "",
    timeout: float = 60,
) -> str:
    return f"""
name = "acme-api"
default_branch = "main"
autonomy = "{autonomy}"
protected_paths = ["migrations/**"]
{extra}

[commands]
test = ['{PY}', '-c', "{test_code}"]

[verify]
gates = ["test"]
timeout_seconds = {timeout}
"""


DEFAULT_TOML = project_toml()


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    """Each test's settings. With PRAT_TEST_DOLT_URL set, the test gets its own
    database on that Dolt server, dropped afterwards; otherwise SQLite."""
    overrides: dict[str, Any] = {}
    name = None
    if TEST_DOLT_URL:
        name = f"prat_t_{uuid.uuid4().hex[:12]}"
        dolt_admin(f"CREATE DATABASE `{name}`")
        overrides["database_url"] = f"{TEST_DOLT_URL.rstrip('/')}/{name}"
    yield load_settings(
        tmp_path / "home",
        stale_run_seconds=0,
        heartbeat_seconds=0.05,
        max_autonomy=Autonomy.HIGH,
        # Lessons add a session per card; the tests that are about them turn them on.
        lessons="off",
        **overrides,
    )
    if name:
        dolt_admin(f"DROP DATABASE IF EXISTS `{name}`")


@pytest.fixture
def db(settings: Settings) -> Iterator[Database]:
    settings.ensure_dirs()
    database = Database(settings.db_url)
    upgrade(database)
    yield database
    database.dispose()


@pytest.fixture
def repo_factory(tmp_path: Path) -> Callable[..., Path]:
    def factory(name: str = "acme-api", **kwargs: Any) -> Path:
        return make_repo(tmp_path / "repos", name, **kwargs)

    return factory


@pytest.fixture
def project_factory(db: Database, repo_factory: Callable[..., Path]) -> Callable[..., Project]:
    def factory(
        name: str = "acme-api", project_toml: str | None = DEFAULT_TOML, **kwargs: Any
    ) -> Project:
        toml = project_toml.replace('"acme-api"', f'"{name}"') if project_toml else None
        repo = repo_factory(name, project_toml=toml, **kwargs)
        with db.session() as session:
            return store.add_project(
                session,
                slug=name,
                checkout_path=repo,
                agent_path=repo.parent / f"{name}-agent",
                spec=load_spec(repo),
            )

    return factory


@pytest.fixture
def project(project_factory: Callable[..., Project]) -> Project:
    return project_factory()


def add_card(db: Database, project: Project, title: str = "Add greet", **kwargs: Any) -> int:
    kwargs.setdefault("ready", True)
    with db.session() as session:
        proj = store.get_project(session, project.id)
        return store.add_card(session, proj, title=title, **kwargs).id


def make_engine(
    settings: Settings,
    db: Database,
    script: Any,
    *,
    reviewer: Any = None,
    security_reviewer: Any = None,
    **overrides: Any,
) -> tuple[Engine, FakeRuntime]:
    if overrides:
        settings = settings.model_copy(update=overrides)
    runtime = (
        script
        if isinstance(script, FakeRuntime)
        else FakeRuntime(script, state_dir=settings.home / "fake-sessions")
    )
    engine = Engine(
        settings,
        db,
        runtime,
        reviewer=reviewer or FakeReviewer(),
        security_reviewer=security_reviewer or FakeSecurityReviewer(),
    )
    return engine, runtime


WRITE_GREET: list[dict[str, Any]] = [
    {"op": "text", "text": "adding greet"},
    {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"},
    {"op": "finish", "message": "added greet"},
]
