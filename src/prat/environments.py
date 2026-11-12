"""Project environments: named ways to run a project, started, stopped and watched by PRAT.

An environment is declared in the project file (``[[environment]]``). PRAT runs it in the
person's checkout, or, as a card preview, in the card's worktree on a free port with the card's
database branch, so a change can be used before it is approved. Each running instance has a
small state file under ``$PRAT_HOME/environments/state``, so restarting PRAT does not lose
track of it; a remembered process is only ever stopped after its start time confirms it is
still the same process.

Machine-specific details (a port, a local host name, extra variables) can be set in
``$PRAT_HOME/environments/<slug>.toml`` without touching the committed project file::

    [dev]
    port = 8080
    env = { APP_URL = "http://shop.test:{port}" }

    [[dev.link]]
    label = "Shop"
    url = "http://shop.test:{port}"

The same file can also define environments of its own (``[[environment]]``, the project file's
format), for a project whose repository should not carry them, and quick links (``[[link]]``,
a label and a URL) to things that are already running, such as a site a local web server
serves. An environment in the committed project file wins over one of the same name here.

``[variables]`` holds values that must never be committed, such as the address of the project's
database on this machine (with its password). A ``[[workspace.database]]`` names one of them in
``url_env``; environments of the checkout get them all, with each database's ``database_env``
pointed at its default branch::

    [variables]
    PRAT_DATABASE_URL = "postgresql+psycopg://postgres:...@127.0.0.1:5480/shop"

Environments run code from the repository (for a preview, code an agent wrote) with your
user's rights, like the project's gates: PRAT's own settings and keys are not passed to them.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import subprocess
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from prat import paths
from prat.db import Database
from prat.git import Git
from prat.git.worktree import rmtree
from prat.proc import ProcessTree, kill_pid_tree, process_started_at, run_tree, spawn
from prat.projects import ENV_VAR_RE, Environment, EnvironmentLink, command_argv
from prat.verification import gate_environment

log = logging.getLogger(__name__)

STATE_DIR = "environments"
LOG_TAIL_BYTES = 12_000
STOP_COMMAND_SECONDS = 30.0
SAME_PROCESS_SECONDS = 2.0


class EnvironmentError(RuntimeError):
    pass


class Override(BaseModel):
    """This machine's changes to one environment, from ``environments/<slug>.toml``."""

    model_config = ConfigDict(extra="forbid")

    port: Annotated[int, Field(ge=1, le=65535)] | None = None
    env: dict[str, str] = Field(default_factory=dict)
    health: Annotated[str, Field(max_length=500)] | None = None
    link: list[EnvironmentLink] | None = None

    @field_validator("env")
    @classmethod
    def _env_names(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            if not ENV_VAR_RE.match(key):
                raise ValueError(f"{key!r} is not an environment variable name")
        return value


def overrides_path(home: Path, slug: str) -> Path:
    return home / STATE_DIR / f"{slug}.toml"


@dataclass
class LocalEnvironments:
    """Everything ``environments/<slug>.toml`` holds for one project on this machine."""

    overrides: dict[str, Override] = field(default_factory=dict)
    environments: list[Environment] = field(default_factory=list)
    links: list[EnvironmentLink] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    problem: str | None = None


def load_local(home: Path, slug: str) -> LocalEnvironments:
    """This machine's environments, quick links and overrides for a project. A bad file is
    reported and nothing in it applies."""
    path = overrides_path(home, slug)
    if not path.is_file():
        return LocalEnvironments()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        envs = [Environment.model_validate(e) for e in raw.pop("environment", [])]
        links = [EnvironmentLink.model_validate(link) for link in raw.pop("link", [])]
        variables = _variables(raw.pop("variables", {}))
        overrides = {name: Override.model_validate(value) for name, value in raw.items()}
    except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError, ValidationError) as exc:
        return LocalEnvironments(problem=f"{path.name} was not applied: {exc}")
    names = [e.name for e in envs]
    if len(set(names)) != len(names):
        return LocalEnvironments(problem=f"{path.name} was not applied: names must be unique")
    return LocalEnvironments(overrides, envs, links, variables)


def _variables(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("[variables] must be a table of names and text values")
    for key, value in raw.items():
        if not ENV_VAR_RE.match(key) or not isinstance(value, str):
            raise ValueError(f"[variables] {key!r} must be a variable name with a text value")
    return dict(raw)


def set_variable(home: Path, slug: str, name: str, value: str) -> None:
    """Record a machine-only variable in ``environments/<slug>.toml``, keeping the rest of the
    file as it is."""
    if not ENV_VAR_RE.match(name):
        raise ValueError(f"{name!r} is not a variable name")
    path = overrides_path(home, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    line = f"{name} = {json.dumps(value)}"
    lines = text.splitlines()
    try:
        start = lines.index("[variables]")
    except ValueError:
        lines += (["", "[variables]"] if lines else ["[variables]"]) + [line]
    else:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
            len(lines),
        )
        for i in range(start + 1, end):
            if lines[i].split("=", 1)[0].strip() == name:
                lines[i] = line
                break
        else:
            lines.insert(end, line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def database_environ(home: Path | None, slug: str) -> dict[str, str]:
    """Where a project database's ``url_env`` is looked up: PRAT's own environment with this
    machine's ``[variables]`` for the project on top."""
    variables = load_local(home, slug).variables if home is not None else {}
    return {**os.environ, **variables}


def checkout_variables(
    home: Path, slug: str, databases: list[Any], overrides: Mapping[str, str] | None = None
) -> dict[str, str]:
    """What an environment run from the person's checkout gets: this machine's variables, and
    each project database's settings pointed at its default branch the way a card's are
    pointed at its branch: ``database_env`` gets the database name and ``app_url_env`` the
    address with the plain driver (``postgresql://``, ``mysql://``) apps expect.
    ``overrides`` replace machine variables (trying another database before switching)."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    local = load_local(home, slug)
    variables = {**local.variables, **(overrides or {})}
    lookup = {**os.environ, **variables}
    for database in databases:
        source = getattr(database, "url_env", None)
        if not source or source not in lookup:
            continue
        try:
            url = make_url(lookup[source])
        except ArgumentError:
            continue
        name_env = getattr(database, "database_env", None)
        if name_env and url.database:
            variables[name_env] = url.database
        app_env = getattr(database, "app_url_env", None)
        if app_env:
            plain = url.set(drivername=url.get_backend_name())
            variables[app_env] = plain.render_as_string(hide_password=False)
    return variables


def load_overrides(home: Path, slug: str) -> tuple[dict[str, Override], str | None]:
    """This machine's overrides for a project, and a problem to show if the file is bad."""
    local = load_local(home, slug)
    return local.overrides, local.problem


def merged(committed: list[Environment], local: LocalEnvironments) -> list[Environment]:
    """The committed environments, then this machine's own (a committed name wins), each
    with this machine's override applied."""
    names = {env.name for env in committed}
    every = [*committed, *(env for env in local.environments if env.name not in names)]
    return [effective(env, local.overrides.get(env.name)) for env in every]


def effective(env: Environment, override: Override | None) -> Environment:
    """The environment with this machine's override applied."""
    if override is None:
        return env
    update: dict[str, Any] = {"env": {**env.env, **override.env}}
    if override.port is not None:
        update["port"] = override.port
    if override.health is not None:
        update["health"] = override.health
    if override.link is not None:
        update["link"] = override.link
    return env.model_copy(update=update)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _fill(text: str, port: int | None) -> str:
    return text.replace("{port}", str(port)) if port is not None else text


@dataclass
class EnvironmentView:
    name: str
    title: str
    state: str  # running, starting, unhealthy, stopped, exited
    card_id: int | None = None
    port: int | None = None
    pid: int | None = None
    started_at: float | None = None
    links: list[tuple[str, str]] = field(default_factory=list)
    message: str = ""
    log_tail: str = ""
    preview: bool = True

    @property
    def running(self) -> bool:
        return self.state in ("running", "starting", "unhealthy")

    @property
    def tone(self) -> str:
        return {
            "running": "ok",
            "starting": "accent",
            "unhealthy": "warn",
            "exited": "bad",
        }.get(self.state, "neutral")


class EnvironmentManager:
    """Starts, stops and reports on environments. One per PRAT server; thread safe."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self._trees: dict[str, ProcessTree] = {}
        self._lock = threading.Lock()

    # Paths and state -------------------------------------------------------------------

    @staticmethod
    def key(name: str, card_id: int | None) -> str:
        return name if card_id is None else f"{name}--card-{card_id}"

    def _dir(self, slug: str, key: str) -> Path:
        return self.home / STATE_DIR / "state" / slug / key

    def _read_state(self, slug: str, key: str) -> dict[str, Any] | None:
        try:
            value = json.loads((self._dir(slug, key) / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _write_state(self, slug: str, key: str, state: Mapping[str, Any]) -> None:
        folder = self._dir(slug, key)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "state.json.tmp"
        tmp.write_text(json.dumps(dict(state)), encoding="utf-8")
        tmp.replace(folder / "state.json")

    def _clear_state(self, slug: str, key: str) -> None:
        with contextlib.suppress(OSError):
            (self._dir(slug, key) / "state.json").unlink()

    @staticmethod
    def _alive(state: Mapping[str, Any]) -> bool:
        pid = int(state.get("pid") or 0)
        started = process_started_at(pid)
        expected = state.get("process_started")
        if started is None or not isinstance(expected, int | float):
            return False
        return abs(started - float(expected)) <= SAME_PROCESS_SECONDS

    def log_path(self, slug: str, name: str, card_id: int | None = None) -> Path:
        return self._dir(slug, self.key(name, card_id)) / "output.log"

    def _tail(self, path: Path) -> str:
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - LOG_TAIL_BYTES))
                return handle.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    # Actions -----------------------------------------------------------------------------

    def start(
        self,
        slug: str,
        env: Environment,
        base: Path,
        *,
        card_id: int | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> EnvironmentView:
        key = self.key(env.name, card_id)
        with self._lock:
            state = self._read_state(slug, key)
            if state is not None and self._alive(state):
                raise EnvironmentError(f"{env.title} is already running")
            base = paths.resolve(base)
            cwd = paths.resolve(base / env.cwd)
            if not paths.is_within(cwd, base) or not cwd.is_dir():
                raise EnvironmentError(f"{env.cwd} is not a folder in {base}")
            port = free_port() if card_id is not None else env.port
            argv = [_fill(arg, port) for arg in command_argv(env.start)]
            variables = {key_: _fill(value, port) for key_, value in env.env.items()}
            if port is not None:
                variables.setdefault("PORT", str(port))
            environ = gate_environment({**variables, **(extra_env or {})})
            folder = self._dir(slug, key)
            folder.mkdir(parents=True, exist_ok=True)
            log_file = folder / "output.log"
            with log_file.open("ab") as out:
                out.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} start: {argv}\n".encode())
                out.flush()
                try:
                    tree = spawn(
                        argv,
                        cwd=cwd,
                        env=environ,
                        stdin=subprocess.DEVNULL,
                        stdout=out,
                        stderr=subprocess.STDOUT,
                    )
                except OSError as exc:
                    raise EnvironmentError(f"could not start {env.title}: {exc}") from exc
            pid = tree.proc.pid
            self._trees[f"{slug}/{key}"] = tree
            self._write_state(
                slug,
                key,
                {
                    "pid": pid,
                    "process_started": process_started_at(pid) or time.time(),
                    "started_at": time.time(),
                    "port": port,
                    "cwd": str(cwd),
                    "card_id": card_id,
                },
            )
        return self.view(slug, env, card_id=card_id)

    def stop(
        self,
        slug: str,
        env: Environment,
        *,
        card_id: int | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> None:
        key = self.key(env.name, card_id)
        with self._lock:
            state = self._read_state(slug, key)
            tree = self._trees.pop(f"{slug}/{key}", None)
            alive = state is not None and self._alive(state)
            if env.stop is not None and alive and state is not None:
                port = state.get("port")
                argv = [_fill(arg, port) for arg in command_argv(env.stop)]
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    run_tree(
                        argv,
                        cwd=state.get("cwd"),
                        env=gate_environment(dict(extra_env or {})),
                        timeout=STOP_COMMAND_SECONDS,
                    )
            if tree is not None:
                tree.kill()
                tree.close()
            elif alive and state is not None:
                kill_pid_tree(int(state["pid"]))
            if state is not None:
                deadline = time.monotonic() + 10
                while self._alive(state) and time.monotonic() < deadline:
                    time.sleep(0.1)
            self._clear_state(slug, key)

    def restart(
        self,
        slug: str,
        env: Environment,
        base: Path,
        *,
        card_id: int | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> EnvironmentView:
        self.stop(slug, env, card_id=card_id, extra_env=extra_env)
        return self.start(slug, env, base, card_id=card_id, extra_env=extra_env)

    # Status ------------------------------------------------------------------------------

    def view(self, slug: str, env: Environment, *, card_id: int | None = None) -> EnvironmentView:
        key = self.key(env.name, card_id)
        state = self._read_state(slug, key)
        result = EnvironmentView(env.name, env.title, "stopped", card_id, preview=env.preview)
        result.log_tail = self._tail(self._dir(slug, key) / "output.log")
        if state is None:
            result.port = env.port if card_id is None else None
            return result
        port = state.get("port")
        result.port = int(port) if isinstance(port, int) else None
        result.pid = int(state.get("pid") or 0) or None
        result.started_at = state.get("started_at")
        if not self._alive(state):
            result.state = "exited"
            result.message = "it stopped on its own; see the log"
            return result
        result.links = [(link.label, _fill(link.url, result.port)) for link in env.link]
        if env.health is None:
            result.state = "running"
            return result
        if _healthy(_fill(env.health, result.port)):
            result.state = "running"
        elif time.time() - float(result.started_at or 0) < env.ready_seconds:
            result.state = "starting"
        else:
            result.state = "unhealthy"
            result.message = f"no answer from {_fill(env.health, result.port)}"
        return result

    def running_previews(self, slug: str) -> list[tuple[str, int]]:
        """(environment name, card id) for every preview of a project that has state."""
        folder = self.home / STATE_DIR / "state" / slug
        found = []
        for entry in folder.glob("*--card-*") if folder.is_dir() else []:
            name, _, card = entry.name.partition("--card-")
            if card.isdigit() and (entry / "state.json").is_file():
                found.append((name, int(card)))
        return found


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:  # noqa: S310
            return int(response.status) < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except (OSError, ValueError):
        return False


# Card previews ---------------------------------------------------------------------------


def preview_path(home: Path, slug: str, card_id: int) -> Path:
    return home / STATE_DIR / "previews" / slug / f"card-{card_id}"


def prepare_preview(agent_path: Path, branch: str, path: Path) -> Path:
    """A fresh, detached checkout of the tip of a card's branch, for a preview. Detached so
    it never holds the branch a later run of the card needs."""
    git = Git(agent_path)
    if not git.ref_exists(f"refs/heads/{branch}"):
        raise EnvironmentError(f"the card's branch {branch} is gone")
    remove_preview(agent_path, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    result = git.run(
        "worktree", "add", "--detach", "--force", str(path), f"refs/heads/{branch}", check=False
    )
    if not result.ok:
        raise EnvironmentError(f"could not check out the card: {result.stderr.strip()}")
    return path


def remove_preview(agent_path: Path, path: Path) -> None:
    git = Git(agent_path)
    if path.exists():
        git.run("worktree", "remove", "--force", str(path), check=False)
        rmtree(path)
    git.run("worktree", "prune", check=False)


# Which environments a project has ---------------------------------------------------------


def committed_spec(db: Database, slug: str) -> tuple[Path, Any, str | None]:
    """A project's checkout and the project file committed on the default branch of PRAT's
    clone (or the snapshot taken at registration), with a problem to show if the committed
    file is not valid. Raises ``LookupError`` for an unknown project. Runs git."""
    from prat import store
    from prat.projects import (
        ProjectFileError,
        ProjectSpec,
        parse_spec,
        show_project_file,
    )

    with db.session() as session:
        try:
            project = store.get_project(session, slug)
        except store.NotFound as exc:
            raise LookupError(str(exc)) from exc
        checkout, agent, branch = (
            Path(project.checkout_path),
            Path(project.agent_path),
            project.default_branch,
        )
        values = dict(project.config or {})
        values.setdefault("name", project.name)
        values.setdefault("default_branch", branch)
    spec = ProjectSpec.model_validate(values)
    problem = None
    if agent.exists():
        shown = show_project_file(Git(agent), branch)
        if shown.ok:
            try:
                spec = parse_spec(shown.stdout)
            except ProjectFileError as exc:
                problem = f"the committed project file is not valid: {exc}"
    return checkout, spec, problem


def project_environments(
    db: Database, home: Path, slug: str
) -> tuple[Path, list[Environment], str | None]:
    """A project's checkout and environments (see :func:`committed_spec`), with this
    machine's overrides. Raises ``LookupError`` for an unknown project. Runs git."""
    checkout, spec, problem = committed_spec(db, slug)
    local = load_local(home, slug)
    return checkout, merged(list(spec.environment), local), problem or local.problem


def checkout_environment_variables(db: Database, home: Path, slug: str) -> dict[str, str]:
    """The variables an environment started in the person's checkout gets."""
    _checkout, spec, _problem = committed_spec(db, slug)
    return checkout_variables(home, slug, list(spec.workspace.database))
