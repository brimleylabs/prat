"""The sandbox with real containers.

Needs docker and PRAT's image (`prat sandbox build`); skipped otherwise. On macOS the
container runtime shares only your home folder, so run these with a base temporary folder
inside it, for example:

    pytest tests/integration/test_sandbox_docker.py --basetemp ~/.cache/prat-pytest
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from conftest import add_card, git, make_engine
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus
from prat.environments import EnvironmentManager
from prat.projects import Environment
from prat.runtime import FakeScript
from prat.sandbox import Sandbox, check, supported
from prat.verification import run_command

pytestmark = pytest.mark.integration

IMAGE = os.environ.get("PRAT_SANDBOX_IMAGE", "prat-sandbox:1")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    if not supported():
        pytest.skip("the sandbox runs on macOS and Linux only")
    problem = check("docker", IMAGE)
    if problem is not None:
        pytest.skip(problem)
    if sys.platform == "darwin" and not tmp_path.is_relative_to(Path.home()):
        pytest.skip("on macOS run with --basetemp inside your home folder")
    (tmp_path / "work").mkdir()
    return tmp_path


def make(root: Path, *, env: dict[str, str] | None = None, network: bool = False) -> Sandbox:
    return Sandbox.create(
        docker="docker",
        image=IMAGE,
        relay_image=IMAGE,
        name=f"test-{root.name}",
        workdir=root / "work",
        home=root / "home",
        network=network,
        env=env,
    )


@pytest.fixture
def sandbox(root: Path) -> Iterator[Sandbox]:
    box = make(root)
    yield box
    box.cleanup()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def leftovers(box: Sandbox) -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label={box.label}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


PROBE = """
import json, os, pathlib, socket
seen = {"uid": os.getuid(), "token": os.environ.get("PRAT_SANDBOX_TEST_TOKEN")}
seen["prat_config"] = pathlib.Path(os.environ["HOST_PRAT_CONFIG"]).exists()
seen["ssh"] = pathlib.Path(os.environ["HOST_SSH"]).exists()
try:
    socket.create_connection(("1.1.1.1", 443), timeout=3).close()
    seen["internet"] = True
except OSError:
    seen["internet"] = False
pathlib.Path("written.txt").write_text("from the container")
print(json.dumps(seen))
"""


def test_a_gate_sees_only_the_worktree_and_no_network(
    sandbox: Sandbox, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAT_SANDBOX_TEST_TOKEN", "must-not-leak")
    (sandbox.workdir / "probe.py").write_text(PROBE, encoding="utf-8")
    env = {
        "HOST_PRAT_CONFIG": str(Path.home() / ".prat" / "config.toml"),
        "HOST_SSH": str(Path.home() / ".ssh"),
    }
    log = root / "gate.log"
    code, timed_out, _, error = run_command(
        ["python", "probe.py"], sandbox.workdir, timeout=120, log_path=log, env=env, sandbox=sandbox
    )
    output = log.read_text(encoding="utf-8")
    assert code == 0 and not timed_out and not error, output
    seen = json.loads(output.strip().splitlines()[-1])
    assert seen == {
        "uid": os.getuid(),
        "token": None,
        "prat_config": False,
        "ssh": False,
        "internet": False,
    }
    written = sandbox.workdir / "written.txt"
    assert written.read_text(encoding="utf-8") == "from the container"
    assert written.stat().st_uid == os.getuid()


def test_a_gate_that_times_out_leaves_no_container(sandbox: Sandbox, root: Path) -> None:
    code, timed_out, _, _ = run_command(
        ["sleep", "120"], sandbox.workdir, timeout=3, log_path=root / "slow.log", sandbox=sandbox
    )
    assert code is None and timed_out
    assert leftovers(sandbox) == []


def test_databases_on_this_machine_are_reached_through_the_relay(root: Path) -> None:
    port = free_port()
    server = socket.create_server(("127.0.0.1", port))
    stop = threading.Event()

    def answer() -> None:
        server.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            with conn:
                conn.sendall(b"pong")

    threading.Thread(target=answer, daemon=True).start()
    env = {"DATABASE_URL": f"postgresql://u:p@127.0.0.1:{port}/app"}
    box = make(root, env=env)
    script = (
        "import os, socket; from urllib.parse import urlparse; "
        "u = urlparse(os.environ['DATABASE_URL']); "
        "s = socket.create_connection((u.hostname, u.port), timeout=10); "
        "print(u.hostname, s.recv(4).decode())"
    )
    try:
        result = subprocess.run(
            box.run_argv(["python", "-c", script], cwd=box.workdir, env=env),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.split() == [f"prat-db-{port}", "pong"]
    finally:
        stop.set()
        server.close()
        box.cleanup()
    assert leftovers(box) == []


def test_the_agents_shell_runs_in_the_sandbox(sandbox: Sandbox) -> None:
    command = 'test -f /.dockerenv && echo "in a container as $(id -u)" | tr a-z A-Z'
    result = subprocess.run(
        ["sh", "-c", sandbox.shell(command, cwd=sandbox.workdir)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"IN A CONTAINER AS {os.getuid()}"


def test_an_environment_answers_on_loopback_and_stops_cleanly(root: Path) -> None:
    box = make(root)
    port = free_port()
    env = Environment.model_validate(
        {
            "name": "web",
            "start": ["python", "-m", "http.server", "{port}", "--bind", "127.0.0.1"],
            "port": port,
            "health": "http://127.0.0.1:{port}/",
            "ready_seconds": 60,
        }
    )
    manager = EnvironmentManager(root / "prat-home", lambda *_: box)
    try:
        manager.start("shop", env, box.workdir)
        state = ""
        for _ in range(120):
            state = manager.view("shop", env).state
            if state == "running":
                break
            time.sleep(0.5)
        assert state == "running", manager.view("shop", env).log_tail
        manager.stop("shop", env)
        assert manager.view("shop", env).state == "stopped"
        assert leftovers(box) == []
    finally:
        box.cleanup()


SANDBOXED_TOML = """
name = "acme-api"
default_branch = "main"
autonomy = "medium"

[commands]
test = ['python', '-c', "import os, sys; sys.exit(0 if os.path.exists('/.dockerenv') else 3)"]

[verify]
gates = ["test"]
timeout_seconds = 120
"""


async def test_a_card_runs_its_commands_and_gates_in_the_sandbox(
    root: Path, settings: Settings, db: Database, project_factory: object
) -> None:
    project: Project = project_factory(project_toml=SANDBOXED_TOML)  # type: ignore[operator]
    card_id = add_card(db, project)
    where = "import os\nopen('where.txt', 'w').write(str(os.path.exists('/.dockerenv')))\n"
    script = [
        {"op": "write", "path": "where.py", "content": where},
        {"op": "bash", "command": "python where.py"},
        {"op": "finish", "message": "recorded where the command ran"},
    ]
    engine, _ = make_engine(
        settings, db, FakeScript.parse(script), sandbox="docker", sandbox_image=IMAGE
    )
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW, result
    # The agent's command and the gate both ran in a container: the gate fails outside one.
    branch = git(Path(project.agent_path), "branch", "--list", f"card/{card_id}-*")
    shown = git(Path(project.agent_path), "show", f"{branch.strip(' *+')}:where.txt")
    assert shown == "True"


def test_a_container_cannot_repoint_git_and_a_planted_git_is_noticed(root: Path) -> None:
    clone = root / "clone"
    clone.mkdir()
    git(clone, "init", "-q", "-b", "main")
    git(clone, "config", "user.name", "Test Person")
    git(clone, "config", "user.email", "person@example.invalid")
    (clone / "app.py").write_text("x = 1\n", encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "start")
    work = root / "card"
    git(clone, "worktree", "add", "-q", "-b", "card", str(work))
    box = Sandbox.create(
        docker="docker",
        image=IMAGE,
        relay_image=IMAGE,
        name=f"git-{root.name}",
        workdir=work,
        home=root / "home",
        network=False,
    )
    pointer = (work / ".git").read_text(encoding="utf-8")
    try:
        attack = subprocess.run(
            box.run_argv(
                ["sh", "-c", "echo 'gitdir: /tmp/fake' > .git; mkdir -p sub/.git"],
                cwd=work,
            ),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert "Read-only file system" in attack.stderr
        assert (work / ".git").read_text(encoding="utf-8") == pointer
        assert box.tampered() == "sub/.git appeared in the worktree"
    finally:
        box.cleanup()
