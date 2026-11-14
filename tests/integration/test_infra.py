"""Environments that use each other, the environment check, and the infra card it files."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import PY, git, make_engine
from prat import infra, store
from prat.config import Settings
from prat.db import Card, Database, Project
from prat.domain import CardKind, CardStatus
from prat.environments import EnvironmentError, EnvironmentManager
from prat.projects import parse_spec
from prat.setup_agent import check_environments

pytestmark = pytest.mark.integration

# A plain TCPServer (see test_setup_card): the API.
API = (
    "import http.server, socketserver, sys\n"
    "port = int(sys.argv[1])\n"
    "server = socketserver.TCPServer(('127.0.0.1', port), http.server.SimpleHTTPRequestHandler)\n"
    "server.serve_forever()\n"
)
# A dev server that serves the page and proxies /api/ to the port in API_PORT, the way a
# Vite dev server proxies to a backend.
PROXY = (
    "import http.server, os, socketserver, sys, urllib.request\n"
    "port, api = int(sys.argv[1]), os.environ.get('API_PORT', '1')\n"
    "class Handler(http.server.BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        code, body = 200, b'page'\n"
    "        if self.path.startswith('/api/'):\n"
    "            try:\n"
    "                url = 'http://127.0.0.1:' + api + self.path[4:]\n"
    "                body = urllib.request.urlopen(url, timeout=2).read()\n"
    "            except Exception:\n"
    "                code, body = 500, b'proxy error'\n"
    "        self.send_response(code)\n"
    "        self.end_headers()\n"
    "        self.wfile.write(body)\n"
    "    def log_message(self, *args):\n"
    "        pass\n"
    "socketserver.TCPServer(('127.0.0.1', port), Handler).serve_forever()\n"
)


def environments(api_port: str = "{port:api}") -> str:
    return f"""
[[environment]]
name = "api"
start = ['{PY}', 'api.py', '{{port}}']
port = 18501
health = "http://127.0.0.1:{{port}}/"
ready_seconds = 20

[[environment]]
name = "web"
start = ['{PY}', 'proxy.py', '{{port}}']
port = 18511
env = {{ API_PORT = "{api_port}" }}
health = "http://127.0.0.1:{{port}}/"
checks = ["http://127.0.0.1:{{port}}/api/"]
ready_seconds = 20
"""


def project_file(api_port: str = "{port:api}", *, name: str = "game") -> str:
    return f"""
name = "{name}"
default_branch = "main"
autonomy = "medium"

[commands]
setup = ['{PY}', '-c', "print('installed')"]
test = ['{PY}', '-c', "print('tests ok')"]

[verify]
gates = ["setup", "test"]
timeout_seconds = 60
{environments(api_port)}"""


def worktree(tmp_path: Path) -> Path:
    path = tmp_path / "worktree"
    path.mkdir()
    (path / "api.py").write_text(API, encoding="utf-8")
    (path / "proxy.py").write_text(PROXY, encoding="utf-8")
    return path


# The project file ------------------------------------------------------------------------


def test_an_environment_can_only_use_the_port_of_one_that_exists() -> None:
    spec = parse_spec(project_file())
    web = next(env for env in spec.environment if env.name == "web")
    assert web.needs == ["api"]
    with pytest.raises(Exception, match="no 'db'"):
        parse_spec(project_file("{port:db}"))


def test_environments_cannot_use_each_other_in_a_loop() -> None:
    looped = project_file().replace(
        'health = "http://127.0.0.1:{port}/"\nready_seconds = 20\n\n[[environment]]',
        'health = "http://127.0.0.1:{port}/"\nready_seconds = 20\nenv = { W = "{port:web}" }\n\n'
        "[[environment]]",
        1,
    )
    with pytest.raises(Exception, match="loop: api -> web -> api"):
        parse_spec(looped)


def test_environments_that_never_meet_are_a_gap() -> None:
    unproven = parse_spec(
        project_file("18300").replace('checks = ["http://127.0.0.1:{port}/api/"]\n', "")
    )
    (gap,) = infra.gaps(list(unproven.environment))
    assert gap.startswith("api, web run side by side")
    assert infra.gaps(list(parse_spec(project_file()).environment)) == []
    assert infra.gaps(list(unproven.environment)[:1]) == []  # one environment meets nothing
    assert not infra.InfraReport("game", gaps=[gap]).passed


# Starting them -------------------------------------------------------------------------------


def wait_running(manager: EnvironmentManager, env: Any, **kw: Any) -> None:
    for _ in range(200):
        if manager.view("game", env, **kw).state == "running":
            return
        time.sleep(0.1)


def test_a_preview_starts_the_api_first_and_hands_its_port_on(tmp_path: Path) -> None:
    manager = EnvironmentManager(tmp_path / "home")
    spec = parse_spec(project_file())
    api, web = spec.environment
    folder = worktree(tmp_path)
    with pytest.raises(EnvironmentError, match="uses the port of api"):
        manager.start("game", web, folder, card_id=4)
    try:
        view = manager.start_with_needs("game", web, list(spec.environment), folder, card_id=4)
        ports = manager.ports("game", 4)
        assert set(ports) == {"api", "web"} and ports["web"] == view.port
        assert ports["api"] not in (18501, view.port)  # a preview gets free ports
        wait_running(manager, web, card_id=4)
        assert manager.ports("game") == {}  # the checkout's own are separate
    finally:
        manager.stop("game", web, card_id=4)
        manager.stop("game", api, card_id=4)


def test_the_check_proves_the_dev_server_reaches_its_api(tmp_path: Path) -> None:
    manager = EnvironmentManager(tmp_path / "home")
    checks = check_environments(
        manager, "game", parse_spec(project_file()), worktree(tmp_path), 0, {}
    )
    assert [c.name for c in checks] == ["api", "web"]
    assert all(c.passed for c in checks), [c.detail for c in checks]
    assert "/api/ answered" in checks[1].detail
    assert manager.ports("game", 0) == {}  # everything it started is stopped


def test_a_dev_server_pointed_at_a_fixed_port_fails_the_check(tmp_path: Path) -> None:
    manager = EnvironmentManager(tmp_path / "home")
    checks = check_environments(
        manager, "game", parse_spec(project_file("18300")), worktree(tmp_path), 0, {}
    )
    api, web = checks
    assert api.passed
    assert not web.passed and "/api/ did not answer without an error" in web.detail


# The check and the infra card ------------------------------------------------------------


@pytest.fixture
def game(project_factory: Callable[..., Project]) -> Project:
    """A project whose committed dev server points at a fixed port, cloned by PRAT."""
    return cloned(project_factory, project_file("18300"))


def cloned(project_factory: Callable[..., Project], toml: str) -> Project:
    """A project with PRAT's clone made, as a first card would."""
    project = project_factory(
        name="game", project_toml=toml, files={"api.py": API, "proxy.py": PROXY}
    )
    checkout = Path(project.checkout_path)
    git(checkout.parent, "clone", "-q", str(checkout), project.agent_path)
    return project


def test_a_failed_check_files_one_infra_card(
    settings: Settings, db: Database, game: Project
) -> None:
    manager = EnvironmentManager(settings.home)
    report = infra.run_check(settings, db, manager, "game")
    assert not report.passed and report.card_id is not None
    assert [f.name for f in report.failures] == ["web"]
    with db.session() as session:
        card = session.get(Card, report.card_id)
        assert card is not None and card.kind is CardKind.INFRA
        assert card.status is CardStatus.READY
        assert "**web**" in card.goal and "FAILED" in card.goal
    saved = infra.last_report(settings.home, "game")
    assert saved is not None and saved.card_id == report.card_id and not saved.passed
    assert not infra.check_folder(settings.home, "game").exists()

    again = infra.run_check(settings, db, manager, "game")
    assert again.card_id == report.card_id  # the open card gets the new report
    with db.session() as session:
        card = session.get(Card, report.card_id)
        assert card is not None
        assert [c.kind for c in card.comments] == ["infra"]


def test_a_passing_check_files_nothing(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    cloned(project_factory, project_file())
    report = infra.run_check(settings, db, EnvironmentManager(settings.home), "game")
    assert report.passed and report.card_id is None, report.render()
    assert "Every environment works." in report.render()


async def test_the_infra_card_fixes_the_project_file_and_prat_proves_it(
    settings: Settings, db: Database, game: Project
) -> None:
    report = infra.run_check(settings, db, EnvironmentManager(settings.home), "game")
    assert report.card_id is not None
    prompts: list[str] = []

    def script_for(task: Any) -> Any:
        from prat.runtime import FakeScript

        prompts.append(task.system_prompt)
        return FakeScript.parse(
            [
                {"op": "write", "path": ".prat/project.toml", "content": project_file()},
                {"op": "finish", "message": "The dev server now gets the API's port."},
            ]
        )

    from prat.runtime import FakeRuntime

    engine, _ = make_engine(settings, db, FakeRuntime(script_for))
    result = await engine.run_card(report.card_id)
    assert result.card_status is CardStatus.REVIEW, result
    assert "PRAT's infra agent" in prompts[0] and "did not answer" in prompts[0]
    with db.session() as session:
        card = store.get_card(session, report.card_id)
        verify = next(s for s in card.runs[-1].steps if str(s.name) == "verify")
        gates = {g["name"]: g for g in verify.detail["gates"]}
    assert gates["environment:api"]["passed"] and gates["environment:web"]["passed"]

    await engine.approve(report.card_id)
    fixed = infra.run_check(settings, db, EnvironmentManager(settings.home), "game")
    assert fixed.passed, fixed.render()
