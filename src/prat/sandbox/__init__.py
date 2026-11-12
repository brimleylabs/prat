"""The sandbox: a card's commands run in a container instead of on this machine.

With ``sandbox = "docker"`` in the installation's settings, everything that runs a project's
code for a card runs in a fresh container: the agent's shell commands, the gates, the setup
checks and the project's environments. Each container

* mounts only the card's worktree (read-write) and the clone's git directory (read-only), at
  the same paths as on this machine, plus a home folder of its own for caches;
* sees none of this machine's environment variables or files outside those mounts, so no
  credentials (``~/.ssh``, ``~/.aws``, a GitHub CLI login, API keys);
* runs as this machine's user, with every capability dropped and no new privileges;
* has no network, except the project's databases: a database address on this machine
  (``127.0.0.1:5480``) is reached through a relay container on a network with no route out.

The setup card is the exception: it installs dependencies, so its containers have the
network. Environments have it too, because the browser must reach them.

Git commands the agent runs on their own stay on this machine, where the policy judges them,
so a container never needs to write the git directory.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from prat.policy.shell import ShellParseError, split_commands

if TYPE_CHECKING:
    from prat.config import Settings

CONTAINER_HOME = "/home/prat"
# Inside an environment's container, the port the browser's traffic arrives on. A relay
# passes it to the port the app listens on, which may be bound to 127.0.0.1 only.
PUBLIC_PORT = 18999
DOCKER_TIMEOUT_SECONDS = 60.0
# A database address on this machine, in a URL or a host:port pair.
_LOOPBACK = re.compile(r"(?<![\w.-])(?:127\.0\.0\.1|localhost|\[::1\]):(\d{2,5})\b")
_NAME = re.compile(r"[^a-z0-9_.-]+")


class SandboxError(RuntimeError):
    """The sandbox is configured but cannot be used."""


def supported() -> bool:
    """Containers share this machine's paths and user id only on macOS and Linux."""
    return sys.platform in ("darwin", "linux")


def dockerfile() -> Path:
    """The Dockerfile of PRAT's default sandbox image."""
    return Path(str(resources.files(__package__).joinpath("Dockerfile")))


def check(docker: str, image: str) -> str | None:
    """Why the sandbox cannot run here, or ``None`` when it can."""
    if not supported():
        return "the sandbox runs on macOS and Linux only"
    try:
        result = subprocess.run(
            [docker, "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            timeout=DOCKER_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"docker is not available: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1] if detail else "unknown error"
        if "No such image" in message or "No such object" in message:
            return (
                f"the sandbox image {image} is not on this machine: run `prat sandbox build` "
                f"for PRAT's image, or `docker pull {image}` for a project's own"
            )
        return f"docker cannot be used: {message}"
    return None


def loopback_ports(values: Mapping[str, str]) -> tuple[int, ...]:
    """The ports of every address on this machine named in ``values``."""
    found = {int(m.group(1)) for value in values.values() for m in _LOOPBACK.finditer(value)}
    return tuple(sorted(p for p in found if 0 < p < 65536))


def runs_on_host(command: str) -> bool:
    """True when a shell command is only git commands, which stay on this machine."""
    try:
        commands = split_commands(command)
    except ShellParseError:
        return False
    return bool(commands) and all(
        c.argv and Path(c.argv[0]).name in ("git", "git.exe") and not c.redirects for c in commands
    )


def for_settings(
    settings: Settings,
    *,
    name: str,
    workdir: Path,
    network: bool,
    env: Mapping[str, str] | None = None,
    image: str | None = None,
) -> Sandbox | None:
    """The sandbox for ``workdir`` when the installation uses one, else ``None``. Raises
    :class:`SandboxError` when it is configured but cannot run: PRAT never falls back to
    running the commands on this machine."""
    if settings.sandbox != "docker":
        return None
    chosen = image or settings.sandbox_image
    problem = check(settings.docker_path, chosen)
    if problem is not None:
        raise SandboxError(problem)
    return Sandbox.create(
        docker=settings.docker_path,
        image=chosen,
        relay_image=settings.sandbox_image,
        name=name,
        workdir=workdir,
        home=settings.home,
        network=network,
        env=env,
        memory=settings.sandbox_memory,
        cpus=settings.sandbox_cpus,
    )


def environment_sandboxes(
    settings: Settings,
) -> Callable[[str, int | None, Path, Mapping[str, str]], Sandbox | None] | None:
    """For :class:`prat.environments.EnvironmentManager`: the sandbox each environment runs
    in, with the image its project file names."""
    if settings.sandbox != "docker":
        return None

    def factory(
        slug: str, card_id: int | None, base: Path, env: Mapping[str, str]
    ) -> Sandbox | None:
        from prat.projects import ProjectFileError, parse_spec, project_file_at

        image = None
        found = project_file_at(base)
        if found is not None:
            with contextlib.suppress(OSError, ProjectFileError):
                image = parse_spec(found.read_text(encoding="utf-8")).sandbox.image
        label = f"{slug}-card-{card_id}" if card_id is not None else f"{slug}-main"
        return for_settings(settings, name=label, workdir=base, network=False, env=env, image=image)

    return factory


def _docker(docker: str, *args: str, timeout: float = DOCKER_TIMEOUT_SECONDS) -> str:
    result = subprocess.run(
        [docker, *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        raise SandboxError(f"docker {args[0]}: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


# Settings for every git command PRAT or the agent runs on this machine in a folder a container
# could write: nothing in that folder may make git run a program. ``core.fsmonitor`` names a
# program; checking a submodule's working tree would read that submodule's own settings.
HOST_GIT_CONFIG: tuple[tuple[str, str], ...] = (
    ("core.fsmonitor", "false"),
    ("diff.ignoreSubmodules", "dirty"),
    ("submodule.recurse", "false"),
    ("status.submoduleSummary", "false"),
)


def host_git_config_env() -> dict[str, str]:
    """:data:`HOST_GIT_CONFIG` as ``GIT_CONFIG_COUNT`` variables, which outrank every file."""
    env = {"GIT_CONFIG_COUNT": str(len(HOST_GIT_CONFIG))}
    for i, (key, value) in enumerate(HOST_GIT_CONFIG):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


def git_common_dir(worktree: Path, which: str = "--git-common-dir") -> Path | None:
    """The git directory a worktree shares with its clone (or, with ``--absolute-git-dir``,
    its own), if it has one."""
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--path-format=absolute", which],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    path = result.stdout.strip()
    return Path(path) if result.returncode == 0 and path else None


@dataclass
class Sandbox:
    """The containers of one card (or of one project's environments), named by ``prefix``."""

    docker: str
    image: str
    prefix: str
    workdir: Path
    state_dir: Path
    network: bool = False
    git_dir: Path | None = None
    relay_image: str = ""
    db_ports: tuple[int, ...] = ()
    # The worktree's own git directory, and its .git file as it was before any container ran.
    own_git_dir: Path | None = None
    git_pointer: str | None = None
    memory: str = "4g"
    cpus: float = 2.0
    _prepared: bool = field(default=False, init=False, repr=False)

    @classmethod
    def create(
        cls,
        *,
        docker: str,
        image: str,
        relay_image: str,
        name: str,
        workdir: Path,
        home: Path,
        network: bool,
        env: Mapping[str, str] | None = None,
        memory: str = "4g",
        cpus: float = 2.0,
    ) -> Sandbox:
        prefix = "prat-" + _NAME.sub("-", name.lower()).strip("-.")[:60]
        pointer = workdir / ".git"
        return cls(
            docker=docker,
            image=image,
            prefix=prefix,
            workdir=workdir,
            state_dir=home / "sandbox" / prefix,
            network=network,
            git_dir=git_common_dir(workdir),
            relay_image=relay_image or image,
            db_ports=loopback_ports(env or {}),
            own_git_dir=git_common_dir(workdir, "--absolute-git-dir"),
            git_pointer=pointer.read_text(encoding="utf-8") if pointer.is_file() else None,
            memory=memory,
            cpus=cpus,
        )

    @property
    def label(self) -> str:
        return f"prat.sandbox={self.prefix}"

    @property
    def network_name(self) -> str:
        if self.network:
            return "bridge"
        return f"{self.prefix}-net" if self.db_ports else "none"

    @property
    def home_dir(self) -> Path:
        return self.state_dir / "home"

    # Set up and tear down ----------------------------------------------------------------

    def prepare(self) -> None:
        """Create the home folder and, for databases without the network, the private network
        and one relay per database port. Safe to call again."""
        if self._prepared:
            return
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.chmod(0o700)
        if not self.network and self.db_ports:
            with contextlib.suppress(SandboxError):
                _docker(self.docker, "network", "create", "--internal", "--label", self.label,
                        self.network_name)  # fmt: skip
            for port in self.db_ports:
                self._start_relay(port)
        self._prepared = True

    def _start_relay(self, port: int) -> None:
        name = f"{self.prefix}-db-{port}"
        with contextlib.suppress(SandboxError):
            _docker(self.docker, "rm", "-f", name)
        _docker(
            self.docker, "run", "-d", "--rm", "--name", name, "--label", self.label,
            "--network", "bridge", "--add-host", "host.docker.internal:host-gateway",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
            "--entrypoint", "socat", self.relay_image,
            f"TCP-LISTEN:{port},fork,reuseaddr", f"TCP:host.docker.internal:{port}",
        )  # fmt: skip
        _docker(self.docker, "network", "connect", "--alias", f"prat-db-{port}",
                self.network_name, name)  # fmt: skip

    def remove(self, name: str) -> None:
        with contextlib.suppress(SandboxError, OSError, subprocess.SubprocessError):
            _docker(self.docker, "rm", "-f", name)

    def cleanup(self) -> None:
        """Remove every container and network this sandbox made. Its home folder stays."""
        with contextlib.suppress(SandboxError, OSError, subprocess.SubprocessError):
            ids = _docker(self.docker, "ps", "-aq", "--filter", f"label={self.label}").split()
            if ids:
                _docker(self.docker, "rm", "-f", *ids)
        if self.network_name.startswith(self.prefix):
            with contextlib.suppress(SandboxError, OSError, subprocess.SubprocessError):
                _docker(self.docker, "network", "rm", self.network_name)
        self._prepared = False

    # Commands ------------------------------------------------------------------------------

    def container_env(self, env: Mapping[str, str], *, direct: bool = False) -> dict[str, str]:
        """``env`` as the container sees it: addresses on this machine point at the relays,
        or with ``direct`` (a container with the network) at this machine itself."""

        def repoint(match: re.Match[str]) -> str:
            port = match.group(1)
            host = "host.docker.internal" if direct else f"prat-db-{port}"
            return f"{host}:{port}"

        return {key: _LOOPBACK.sub(repoint, value) for key, value in env.items()}

    def env_file(self, env: Mapping[str, str], tag: str, *, direct: bool = False) -> Path:
        """Write ``env`` for ``docker run --env-file``, readable only by this user, so values
        never appear on a command line."""
        self.prepare()
        path = self.state_dir / f"env-{_NAME.sub('-', tag)}"
        lines = [f"HOME={CONTAINER_HOME}"]
        for key, value in self.container_env(env, direct=direct).items():
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and "\n" not in value:
                lines.append(f"{key}={value}")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return path

    def run_argv(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        name: str | None = None,
        publish: int | None = None,
        tag: str = "run",
    ) -> list[str]:
        """The ``docker run`` command that runs ``argv`` in this sandbox and removes the
        container when it ends. ``publish`` makes the container answer on that port of
        this machine's loopback address (for environments)."""
        self.prepare()
        direct = self.network or publish is not None
        env_path = self.env_file(env or {}, tag, direct=direct)
        # Linux and macOS only (see supported()); looked up by name for type checks elsewhere.
        uid, gid = getattr(os, "getuid", lambda: 1000)(), getattr(os, "getgid", lambda: 1000)()
        user = f"{uid}:{gid}"
        command = [
            self.docker, "run", "--rm", "-i", "--init",
            "--label", self.label,
            "--network", "bridge" if publish is not None else self.network_name,
            "--user", user,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "2048", "--memory", self.memory, "--cpus", str(self.cpus),
            "--env-file", str(env_path),
            "-v", f"{self.workdir}:{self.workdir}",
            "-v", f"{self.home_dir}:{CONTAINER_HOME}",
            "-w", str(cwd),
        ]  # fmt: skip
        if self.git_dir is not None and not self.git_dir.is_relative_to(self.workdir):
            command += ["-v", f"{self.git_dir}:{self.git_dir}:ro"]
        dot_git = self.workdir / ".git"
        if dot_git.exists():
            # The worktree's .git (a pointer file, or a clone's whole git directory) decides
            # what git on this machine reads: a container must not be able to change it.
            command += ["-v", f"{dot_git}:{dot_git}:ro"]
        if direct:
            command += ["--add-host", "host.docker.internal:host-gateway"]
        if name is not None:
            command += ["--name", name]
        if publish is not None:
            command += ["-p", f"127.0.0.1:{publish}:{PUBLIC_PORT}"]
            relay = (
                f'socat TCP-LISTEN:{PUBLIC_PORT},fork,reuseaddr TCP:127.0.0.1:{publish} & exec "$@"'
            )
            return [*command, self.image, "sh", "-c", relay, "prat-environment", *argv]
        return [*command, self.image, *argv]

    def host_git_env(self) -> dict[str, str]:
        """Variables for git commands the agent runs on this machine in the worktree: the git
        directory is named outright, so no ``.git`` a container planted in a subfolder is ever
        found, and nothing in the repository's settings can run a program."""
        env = host_git_config_env()
        if self.own_git_dir is not None:
            env.update(GIT_DIR=str(self.own_git_dir), GIT_WORK_TREE=str(self.workdir))
        return env

    def tampered(self) -> str | None:
        """What a container changed that git on this machine would trust, or ``None``: the
        worktree's .git file, or a .git anywhere below the worktree's top."""
        pointer = self.workdir / ".git"
        if self.git_pointer is not None:
            try:
                now = pointer.read_text(encoding="utf-8") if pointer.is_file() else None
            except OSError:
                now = None
            if now != self.git_pointer:
                return "the worktree's .git file was changed"
        for folder, dirs, files in os.walk(self.workdir):
            top = Path(folder) == self.workdir
            if not top and (".git" in dirs or ".git" in files):
                relative = Path(folder).relative_to(self.workdir).as_posix()
                return f"{relative}/.git appeared in the worktree"
            dirs[:] = [d for d in dirs if d != ".git"]
        return None

    def shell(self, command: str, *, cwd: Path, env: Mapping[str, str] | None = None) -> str:
        """A shell command line that runs ``command`` in this sandbox, for the agent's shell."""
        return shlex.join(self.run_argv(["sh", "-c", command], cwd=cwd, env=env, tag="shell"))
