"""Environments: real processes started, watched and stopped by PRAT."""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from prat.environments import (
    EnvironmentError,
    EnvironmentManager,
    effective,
    load_overrides,
    overrides_path,
)
from prat.proc import process_started_at
from prat.projects import Environment, parse_spec

pytestmark = pytest.mark.integration

SERVER = Environment.model_validate(
    {
        "name": "web",
        "label": "Web app",
        "start": "{python} -m http.server {port} --bind 127.0.0.1",
        "port": 1,
        "health": "http://127.0.0.1:{port}/",
        "ready_seconds": 20,
        "link": [{"label": "App", "url": "http://127.0.0.1:{port}/"}],
    }
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def server(port: int) -> Environment:
    return SERVER.model_copy(update={"port": port})


def wait_for(manager: EnvironmentManager, env: Environment, state: str, **kw: int) -> str:
    seen = ""
    for _ in range(150):
        seen = manager.view("shop", env, **kw).state
        if seen == state:
            return seen
        time.sleep(0.1)
    return seen


def test_start_watch_and_stop(tmp_path: Path) -> None:
    manager = EnvironmentManager(tmp_path / "home")
    env = server(free_port())
    repo = tmp_path / "repo"
    repo.mkdir()
    assert manager.view("shop", env).state == "stopped"
    manager.start("shop", env, repo)
    assert wait_for(manager, env, "running") == "running"
    view = manager.view("shop", env)
    assert view.links == [("App", f"http://127.0.0.1:{env.port}/")]
    assert view.tone == "ok" and view.pid
    with pytest.raises(EnvironmentError, match="already running"):
        manager.start("shop", env, repo)
    pid = view.pid
    manager.stop("shop", env)
    assert manager.view("shop", env).state == "stopped"
    assert process_started_at(pid or 0) is None


def test_a_restarted_prat_can_still_stop_it(tmp_path: Path) -> None:
    env = server(free_port())
    repo = tmp_path / "repo"
    repo.mkdir()
    first = EnvironmentManager(tmp_path / "home")
    first.start("shop", env, repo)
    assert wait_for(first, env, "running") == "running"
    pid = first.view("shop", env).pid
    # A new manager (PRAT restarted) holds no process handle, only the state file.
    second = EnvironmentManager(tmp_path / "home")
    assert second.view("shop", env).state == "running"
    second.stop("shop", env)
    deadline = time.monotonic() + 10
    while process_started_at(pid or 0) is not None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert process_started_at(pid or 0) is None
    first._trees.clear()  # the old handle is gone with the old PRAT


def test_a_process_that_dies_shows_as_exited(tmp_path: Path) -> None:
    manager = EnvironmentManager(tmp_path / "home")
    env = Environment.model_validate(
        {"name": "job", "start": ["{python}", "-c", "print('hello from the job')"]}
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    manager.start("shop", env, repo)
    assert wait_for(manager, env, "exited") == "exited"
    assert "hello from the job" in manager.view("shop", env).log_tail
    manager.stop("shop", env)
    assert manager.view("shop", env).state == "stopped"


def test_a_preview_gets_its_own_port_and_variables(tmp_path: Path) -> None:
    manager = EnvironmentManager(tmp_path / "home")
    env = Environment.model_validate(
        {
            "name": "web",
            "start": [
                "{python}",
                "-c",
                "import os, time; e = os.environ; "
                "print('db=' + e['APP_DB_URL'], 'port=' + e['PORT'], flush=True); time.sleep(60)",
            ],
            "port": 5000,
        }
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    view = manager.start(
        "shop", env, worktree, card_id=7, extra_env={"APP_DB_URL": "mysql://x/shop/card-7"}
    )
    assert view.card_id == 7 and view.port and view.port != 5000
    for _ in range(100):
        if "db=mysql" in manager.view("shop", env, card_id=7).log_tail:
            break
        time.sleep(0.1)
    tail = manager.view("shop", env, card_id=7).log_tail
    assert "db=mysql://x/shop/card-7" in tail and f"port={view.port}" in tail
    assert manager.running_previews("shop") == [("web", 7)]
    assert manager.view("shop", env).state == "stopped"  # the checkout's own is separate
    manager.stop("shop", env, card_id=7)
    assert manager.running_previews("shop") == []


def test_cwd_must_stay_inside_the_repository(tmp_path: Path) -> None:
    manager = EnvironmentManager(tmp_path / "home")
    env = Environment.model_validate({"name": "web", "start": "x", "cwd": "missing"})
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(EnvironmentError, match="not a folder"):
        manager.start("shop", env, repo)
    with pytest.raises(ValueError, match=r"escapes|relative"):
        Environment.model_validate({"name": "web", "start": "x", "cwd": "../elsewhere"})


def test_machine_overrides(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = overrides_path(home, "shop")
    path.parent.mkdir(parents=True)
    path.write_text(
        '[web]\nport = 8080\nenv = { APP_URL = "http://shop.test:{port}" }\n'
        '[[web.link]]\nlabel = "Shop"\nurl = "http://shop.test:{port}"\n',
        encoding="utf-8",
    )
    overrides, problem = load_overrides(home, "shop")
    assert problem is None
    env = effective(SERVER, overrides.get("web"))
    assert env.port == 8080 and env.env["APP_URL"] == "http://shop.test:{port}"
    assert [link.label for link in env.link] == ["Shop"]
    path.write_text("[web]\nhost = 'x'\n", encoding="utf-8")
    overrides, problem = load_overrides(home, "shop")
    assert overrides == {} and problem and "was not applied" in problem


def test_environments_in_the_project_file() -> None:
    spec = parse_spec(
        """
name = "shop"

[[environment]]
name = "dev"
label = "Local dev"
start = "pnpm dev --port {port}"
cwd = "apps/web"
port = 5173
health = "http://localhost:{port}/healthz"

[[environment.link]]
label = "App"
url = "http://localhost:{port}"

[[environment.link]]
label = "Admin"
url = "http://localhost:{port}/admin"

[[environment]]
name = "worker"
start = ["python", "-m", "worker"]
preview = false
"""
    )
    dev, worker = spec.environment
    assert dev.title == "Local dev" and dev.cwd == "apps/web" and len(dev.link) == 2
    assert worker.title == "worker" and not worker.preview
    twice = 'name = "x"\n[[environment]]\nname = "a"\nstart = "x"\n'
    with pytest.raises(ValueError, match="own name"):
        parse_spec(twice + '[[environment]]\nname = "a"\nstart = "y"\n')
    with pytest.raises(ValueError, match="http"):
        parse_spec(twice + '[[environment.link]]\nlabel = "L"\nurl = "file:///etc"\n')


def test_the_env_commands(tmp_path: Path, repo_factory: Callable[..., Path]) -> None:
    from typer.testing import CliRunner

    from conftest import DEFAULT_TOML
    from prat.cli import app

    port = _free()
    extra = (
        '\n[[environment]]\nname = "web"\n'
        'start = "{python} -m http.server {port} --bind 127.0.0.1"\n'
        f"port = {port}\n"
        '[[environment.link]]\nlabel = "App"\nurl = "http://127.0.0.1:{port}/"\n'
    )
    repo = repo_factory(
        "envapp", project_toml=DEFAULT_TOML.replace('"acme-api"', '"envapp"') + extra
    )
    runner = CliRunner()
    home = tmp_path / "cli-home"

    def cli(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, ["--home", str(home), *args], env={"COLUMNS": "200"})
        assert result.exit_code == code, result.output
        return result.output

    cli("init")
    cli("project", "add", str(repo))
    assert "stopped" in cli("env", "list", "envapp")
    started = cli("env", "start", "envapp", "web")
    assert "started" in started and f"http://127.0.0.1:{port}/" in started
    assert "running" in cli("env", "list", "envapp")
    assert "start:" in cli("env", "log", "envapp", "web")
    assert "stopped" in cli("env", "stop", "envapp", "web")
    assert "stopped" in cli("env", "list", "envapp")
    assert "no environment 'nope'" in cli("env", "start", "envapp", "nope", code=1)


def _free() -> int:
    return free_port()
