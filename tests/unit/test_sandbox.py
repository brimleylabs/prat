"""The sandbox's commands, without running docker."""

from __future__ import annotations

import os
import shlex
import stat
from pathlib import Path

import pytest

from prat.config import load_settings
from prat.runtime.claude import sandboxed_input
from prat.sandbox import (
    CONTAINER_HOME,
    PUBLIC_PORT,
    Sandbox,
    for_settings,
    loopback_ports,
)

DB_URL = "postgresql://app:pw@127.0.0.1:5480/shop"


def make(tmp_path: Path, *, network: bool = False, env: dict[str, str] | None = None) -> Sandbox:
    work = tmp_path / "worktree"
    work.mkdir(exist_ok=True)
    return Sandbox.create(
        docker="docker",
        image="prat-sandbox:1",
        relay_image="prat-sandbox:1",
        name="Shop API-card-7",
        workdir=work,
        home=tmp_path / "home",
        network=network,
        env=env,
    )


def option(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_loopback_ports_are_found_in_addresses() -> None:
    env = {
        "DATABASE_URL": DB_URL,
        "CACHE": "redis://localhost:6379/0",
        "REMOTE": "https://db.example.com:5432/x",
        "NOT_A_PORT": "127.0.0.1",
    }
    assert loopback_ports(env) == (5480, 6379)


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "git status",
        "git add -A && git commit -m 'x'",
        "/usr/bin/git log --oneline",
        "git rebase --exec ./planted.sh HEAD~1",
        "git bisect run ./planted.sh",
        "git --git-dir=planted commit -m x",
        "EDITOR=./planted.sh git commit",
        "echo 'unterminated",
    ],
)
def test_no_command_stays_on_the_host(command: str) -> None:
    def shell(text: str) -> str:
        return f"SANDBOXED {text}"

    assert sandboxed_input("Bash", {"command": command}, shell) == {
        "command": f"SANDBOXED {command}"
    }


def test_without_databases_the_container_has_no_network(tmp_path: Path) -> None:
    sandbox = make(tmp_path)
    argv = sandbox.run_argv(["pytest", "-q"], cwd=sandbox.workdir, env={"A": "1"})
    assert option(argv, "--network") == "none"
    assert argv[-3:] == ["prat-sandbox:1", "pytest", "-q"]
    for required in ("--rm", "--init", "--cap-drop", "--security-opt"):
        assert required in argv
    if hasattr(os, "getuid"):
        assert option(argv, "--user") == f"{os.getuid()}:{os.getgid()}"


def test_only_the_worktree_and_a_home_folder_are_mounted(tmp_path: Path) -> None:
    sandbox = make(tmp_path)
    argv = sandbox.run_argv(["true"], cwd=sandbox.workdir)
    mounts = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-v"]
    assert f"{sandbox.workdir}:{sandbox.workdir}" in mounts
    assert f"{sandbox.home_dir}:{CONTAINER_HOME}" in mounts
    assert len(mounts) == 2  # not a git repository here, so no git directory either
    assert option(argv, "-w") == str(sandbox.workdir)


def test_databases_are_reached_through_relays_on_a_private_network(tmp_path: Path) -> None:
    sandbox = make(tmp_path, env={"DATABASE_URL": DB_URL})
    assert sandbox.db_ports == (5480,)
    assert sandbox.network_name == f"{sandbox.prefix}-net"
    assert sandbox.container_env({"DATABASE_URL": DB_URL}) == {
        "DATABASE_URL": "postgresql://app:pw@prat-db-5480:5480/shop"
    }


def test_with_the_network_databases_are_reached_directly(tmp_path: Path) -> None:
    sandbox = make(tmp_path, network=True, env={"DATABASE_URL": DB_URL})
    argv = sandbox.run_argv(["true"], cwd=sandbox.workdir, env={"DATABASE_URL": DB_URL})
    assert option(argv, "--network") == "bridge"
    assert "host.docker.internal:host-gateway" in argv
    env_file = Path(option(argv, "--env-file")).read_text(encoding="utf-8")
    assert "DATABASE_URL=postgresql://app:pw@host.docker.internal:5480/shop" in env_file


def test_variables_go_in_a_private_file_not_on_the_command_line(tmp_path: Path) -> None:
    sandbox = make(tmp_path)
    secret = "s3cr3t-value"
    argv = sandbox.run_argv(["true"], cwd=sandbox.workdir, env={"TOKEN": secret})
    assert not any(secret in arg for arg in argv)
    path = Path(option(argv, "--env-file"))
    text = path.read_text(encoding="utf-8")
    assert f"TOKEN={secret}" in text
    assert f"HOME={CONTAINER_HOME}" in text
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_this_machines_environment_is_not_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghx-not-for-containers")
    sandbox = make(tmp_path)
    argv = sandbox.run_argv(["true"], cwd=sandbox.workdir, env={"A": "1"})
    text = Path(option(argv, "--env-file")).read_text(encoding="utf-8")
    assert "GITHUB_TOKEN" not in text
    assert "-e" not in argv


def test_an_environment_publishes_its_port_on_loopback_only(tmp_path: Path) -> None:
    sandbox = make(tmp_path)
    argv = sandbox.run_argv(
        ["python", "-m", "http.server", "8123"], cwd=sandbox.workdir, name="web", publish=8123
    )
    assert option(argv, "-p") == f"127.0.0.1:8123:{PUBLIC_PORT}"
    assert option(argv, "--name") == "web"
    relay = argv[argv.index("-c") + 1]
    assert f"TCP-LISTEN:{PUBLIC_PORT}" in relay and "TCP:127.0.0.1:8123" in relay
    assert argv[-4:] == ["python", "-m", "http.server", "8123"]


def test_shell_commands_survive_quoting(tmp_path: Path) -> None:
    sandbox = make(tmp_path)
    command = 'echo "it\'s $HOME" && ls -la | head -2'
    wrapped = sandbox.shell(command, cwd=sandbox.workdir)
    argv = shlex.split(wrapped)
    assert argv[0] == "docker" and argv[1] == "run"
    assert argv[-3:] == ["sh", "-c", command]


def test_the_policy_hook_moves_approved_shell_commands_into_the_sandbox() -> None:
    def shell(command: str) -> str:
        return f"SANDBOXED {command}"

    moved = sandboxed_input("Bash", {"command": "pytest -q", "timeout": 5}, shell)
    assert moved == {"command": "SANDBOXED pytest -q", "timeout": 5}
    assert sandboxed_input("Bash", {"command": "  "}, shell) is None
    assert sandboxed_input("Read", {"file_path": "a.py"}, shell) is None
    assert sandboxed_input("Bash", {"command": "pytest"}, None) is None


def test_the_sandbox_is_off_by_default(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "home")
    assert settings.sandbox == "off"
    assert for_settings(settings, name="x", workdir=tmp_path, network=False) is None


def test_a_configured_sandbox_that_cannot_run_fails_closed(tmp_path: Path) -> None:
    from prat.sandbox import SandboxError

    settings = load_settings(
        tmp_path / "home", sandbox="docker", docker_path=str(tmp_path / "no-docker-here")
    )
    with pytest.raises(SandboxError):
        for_settings(settings, name="x", workdir=tmp_path, network=False)
