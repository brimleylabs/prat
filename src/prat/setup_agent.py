"""The setup agent: one card that makes a freshly added project runnable by PRAT.

A project added without a project file gets a setup card (kind ``setup``). It runs through the
ordinary engine (worktree, policy, journal, gates, reviewers, your approval) with two
differences:

- Its agent is the one agent allowed to write ``.prat/``. It works out the project's stack and
  proposes ``.prat/project.toml`` (commands, gates, environments, notes) and an idempotent
  ``.prat/setup.sh`` that installs what the project needs.
- Its verify step proves the proposal: the proposed project file must load, its gates (setup
  first) must pass in the card's worktree, and every proposed environment must start and answer
  its health check on a free port. The results go to the reviewer like any gate.

When this machine has a Doltgres server for projects (``project_database_url``), PRAT creates a
database for the project there first, records its address as a machine-only variable
(``PRAT_DATABASE_URL`` in ``environments/<slug>.toml``, never committed) and gives it to the
agent, which declares it as a ``[[workspace.database]]`` so every later card gets its own branch
of the data.

Approving the card merges the project file like any change; PRAT then scans the project to seed
its knowledge.
"""

from __future__ import annotations

import contextlib
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prat.config import Settings
from prat.domain import Autonomy
from prat.environments import EnvironmentError, EnvironmentManager, set_variable
from prat.projects import (
    PROJECT_FILE_REL,
    ProjectFileError,
    ProjectSpec,
    parse_spec,
    project_file_at,
)
from prat.verification import GateResult

DATABASE_VARIABLE = "PRAT_DATABASE_URL"
SETUP_FILE_REL = ".prat/setup.sh"
ENVIRONMENT_POLL_SECONDS = 1.0
NO_HEALTH_WAIT_SECONDS = 5.0

SETUP_TITLE = "Set up {name} for PRAT"
SETUP_GOAL = """\
Make this repository runnable and checkable by PRAT on this machine, for whatever stack it uses:
write the project file PRAT reads and a setup script that installs everything the project needs,
so that every later card can install, test and run the app the same way."""
SETUP_CRITERIA = """\
- .prat/project.toml loads (name, default_branch, autonomy = "medium", notes), defines setup and
  test commands (and lint or build where the project has them), and lists them as verify gates
  with setup first. Every gate passes in a fresh worktree.
- .prat/setup.sh installs the project's dependencies (language runtime packages, node modules,
  build steps, database migrations) into the worktree, can be run again safely, and fails with a
  clear message when a tool it needs is missing.
- Each way to run the app is an [[environment]] with a start command, a port or {port}, and a
  health URL; PRAT starts each one on a free port and it answers.
- Nothing secret is committed: credentials and machine-specific addresses come from environment
  variables, and the notes say which ones the app reads.
- The notes give a future agent the essentials: the layout, how to run and test, and anything
  that must never be run (live services, production data)."""

SETUP_ROLE = """\
You are PRAT's setup agent. A project was just added to PRAT, and PRAT cannot work on it until
it knows how to install, test and run it. You work in a git worktree of the project created for
this card, and you are the only agent allowed to write the project's .prat/ folder.

Work out the stack from the repository itself (manifests, lock files, READMEs, CI workflows,
Dockerfiles, existing scripts). Prefer the project's own tools and versions. Then write:

1. .prat/setup.sh, a POSIX shell script that PRAT runs from the repository root before every
   check. It installs into the worktree (a virtual environment, node_modules, vendor), never
   system-wide and never with sudo; it is safe to run again; it checks for the runtimes it needs
   and exits non-zero with a clear message when one is missing. If the app has a database, it
   prepares it (migrations) using the database variable described below.
2. .prat/project.toml: name, default_branch, autonomy = "medium", notes, [commands] with setup =
   "sh .prat/setup.sh", test (and lint, build where they exist), [verify] gates starting with
   setup, and an [[environment]] for each way to run the app: start (a command list or string,
   run without a shell; use {port} for the port PRAT assigns), cwd, env, port, and health (an
   http://127.0.0.1:{port}/... URL that answers when the app is up).

Run your setup script and the gates yourself and fix them until they pass, then start each
environment the way PRAT will and check its health URL with curl on 127.0.0.1 in the same command,
stopping it there with kill $! (each tool call is a new shell). Keep logs inside the worktree.
Commit your work."""

PROJECT_FILE_FORMAT = """\
# The project file format (PRAT reads exactly these keys)
name = "shop"
default_branch = "main"
autonomy = "medium"
notes = \"\"\"Short facts every agent should know.\"\"\"

[commands]
setup = "sh .prat/setup.sh"
test = ".venv/bin/python -m pytest -q"
lint = ".venv/bin/ruff check ."
build = "npm run build"

[verify]
gates = ["setup", "test", "lint"]
timeout_seconds = 900

[[environment]]
name = "dev"                     # lowercase letters, digits, dashes
label = "Web app"
start = [".venv/bin/uvicorn", "app.main:app", "--port", "{port}"]
cwd = "."
port = 8000
env = { APP_ENV = "development" }
health = "http://127.0.0.1:{port}/health"
ready_seconds = 60

[[workspace.database]]           # only when PRAT gave you a database (see below)
name = "app"
kind = "doltgres"
url_env = "PRAT_DATABASE_URL"
app_url_env = "DATABASE_URL"     # the variable the app reads its full database URL from
# database_env = "DB_NAME"       # only for an app that reads just the database name"""


def setup_system_prompt(
    *,
    name: str,
    default_branch: str,
    autonomy: Autonomy,
    database: bool,
    repo_instructions: str | None = None,
) -> str:
    parts = [SETUP_ROLE]
    if repo_instructions:
        parts.append(repo_instructions)
    facts = [
        f"# Project: {name}",
        f"Default branch: {default_branch}.",
        f"Autonomy level for this run: {autonomy}. You may run local programs and scripts; "
        "downloading packages directly (pip, npm, curl to the internet) is denied, so put "
        "installs in .prat/setup.sh and run it with `sh .prat/setup.sh`.",
    ]
    if database:
        facts.append(
            f"PRAT created a database for this project and put its address in the variable "
            f"{DATABASE_VARIABLE} (a Postgres URL on PRAT's Doltgres server). If the app uses a "
            "database, use that one: declare it as the [[workspace.database]] shown below, with "
            "app_url_env set to the variable the app reads its database URL from (PRAT puts a "
            "plain postgresql:// URL there, pointed at the right branch), and never write the "
            "address into a file. Every later card gets its own branch of this database, so the "
            "setup script and the app must read that variable, not a fixed address."
        )
    else:
        facts.append(
            "PRAT has no database server for projects on this machine. If the app needs a "
            "database, use what the repository provides for development (SQLite or similar) "
            "and say so in the notes."
        )
    parts += [
        "\n".join(facts),
        PROJECT_FILE_FORMAT,
        "\n".join(
            [
                "# Rules",
                "- Work only inside the current directory, the card's worktree.",
                "- Commit your work with clear messages. Never push.",
                "- Do not read or write secret files (.env, keys, credentials), and never put a "
                "password or token in a committed file.",
                "- Change only .prat/ unless the project cannot run without a small fix; say "
                "why in your summary if you change anything else.",
                "- If PRAT's policy denies a call, do not try to get around it.",
                "- Finish with a short summary: the stack you found, the commands, the "
                "environments and what you checked.",
            ]
        ),
        "# If you are blocked\nIf the project needs something only a person can provide (a "
        "licence, an account, a missing runtime that is not installed), make the last line of "
        "your final message:\n\nBLOCKED: <one specific question>",
    ]
    return "\n\n".join(parts) + "\n"


SETUP_TASK = "Set this project up for PRAT as described in your instructions."


# Proving the proposal ----------------------------------------------------------------------


def proposed_spec(worktree: Path) -> tuple[ProjectSpec | None, str | None]:
    """The project file the setup agent proposed, or why there is none that loads."""
    path = project_file_at(worktree) or worktree / PROJECT_FILE_REL
    if not path.is_file():
        return None, f"no {PROJECT_FILE_REL} was written"
    try:
        spec = parse_spec(path.read_text(encoding="utf-8"), source=PROJECT_FILE_REL)
    except (OSError, ProjectFileError) as exc:
        return None, str(exc)
    if not spec.verify.gates:
        return spec, f"{PROJECT_FILE_REL} lists no verify gates"
    return spec, None


@dataclass
class EnvironmentCheck:
    name: str
    passed: bool
    port: int | None
    seconds: float
    detail: str
    log_tail: str = ""

    def as_gate(self) -> GateResult:
        """The check as a gate result, so the reviewer and the board show it next to the gates
        and a failure fails the run like a gate."""
        return GateResult(
            name=f"environment:{self.name}",
            command=f"start {self.name} and check its health",
            exit_code=0 if self.passed else 1,
            passed=self.passed,
            timed_out=False,
            cancelled=False,
            duration_seconds=round(self.seconds, 1),
            output_tail=(self.detail + ("\n" + self.log_tail if self.log_tail else ""))[-4000:],
            log_path="",
        )


def problem_gate(problem: str) -> GateResult:
    """A proposal that does not load, as a failed gate."""
    return GateResult(
        name="project-file",
        command=f"load {PROJECT_FILE_REL}",
        exit_code=1,
        passed=False,
        timed_out=False,
        cancelled=False,
        duration_seconds=0.0,
        output_tail=problem,
        log_path="",
    )


def _answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310
            return int(response.status) < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except (OSError, ValueError):
        return False


def check_environments(
    manager: EnvironmentManager,
    slug: str,
    spec: ProjectSpec,
    worktree: Path,
    card_id: int,
    variables: Mapping[str, str],
    *,
    stop: Callable[[], bool] = lambda: False,
) -> list[EnvironmentCheck]:
    """Start each proposed environment in the worktree on a free port, wait for its health
    URL to answer (or, without one, for it to stay up), and stop it again."""
    checks: list[EnvironmentCheck] = []
    for env in spec.environment:
        started = time.monotonic()
        try:
            view = manager.start(slug, env, worktree, card_id=card_id, extra_env=variables)
        except EnvironmentError as exc:
            checks.append(EnvironmentCheck(env.name, False, None, 0.0, f"did not start: {exc}"))
            continue
        port = view.port
        passed, detail = False, ""
        try:
            if env.health:
                url = env.health.replace("{port}", str(port or ""))
                deadline = started + env.ready_seconds
                while time.monotonic() < deadline and not stop():
                    if _answers(url):
                        passed, detail = True, f"{url} answered"
                        break
                    if not manager.view(slug, env, card_id=card_id).running:
                        detail = "the process exited before answering"
                        break
                    time.sleep(ENVIRONMENT_POLL_SECONDS)
                else:
                    detail = detail or f"{url} did not answer within {env.ready_seconds:.0f}s"
            else:
                time.sleep(NO_HEALTH_WAIT_SECONDS)
                passed = manager.view(slug, env, card_id=card_id).running
                detail = "still running (no health URL to check)" if passed else "exited"
            log_tail = manager.view(slug, env, card_id=card_id).log_tail if not passed else ""
        finally:
            with contextlib.suppress(EnvironmentError):
                manager.stop(slug, env, card_id=card_id, extra_env=variables)
        checks.append(
            EnvironmentCheck(
                env.name, passed, port, time.monotonic() - started, detail, log_tail[-2000:]
            )
        )
    return checks


# The project database --------------------------------------------------------------------


def database_name(slug: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", slug.lower())[:60] or "project"


def create_project_database(settings: Settings, slug: str) -> str | None:
    """Create the project's database on this machine's Doltgres server (if one is set and the
    database does not exist yet) and record its address as the machine-only variable
    ``PRAT_DATABASE_URL``. Returns the address, or None without a server."""
    if settings.project_database_url is None:
        return None
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    server = make_url(settings.project_database_url.get_secret_value())
    name = database_name(slug)
    engine = create_engine(server, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            found = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            ).first()
            if found is None:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()
    address = server.set(database=name).render_as_string(hide_password=False)
    set_variable(settings.home, slug, DATABASE_VARIABLE, address)
    return address


def describe(checks: Sequence[EnvironmentCheck]) -> str:
    return "; ".join(f"{c.name}: {'ok' if c.passed else c.detail}" for c in checks)


# Starting it ---------------------------------------------------------------------------------


def needs_setup(checkout: Path) -> bool:
    """Whether a project has no committed project file for PRAT yet."""
    return project_file_at(checkout) is None


def add_setup_card(session: Any, project: Any, settings: Settings, *, database: bool = True) -> Any:
    """Create the project's setup card, ready to run, after making its database (when this
    machine has a database server for projects, unless ``database`` is False because the
    caller made it already). Returns the card."""
    from prat import store
    from prat.domain import CardKind, Complexity

    if database:
        create_project_database(settings, project.slug)
    return store.add_card(
        session,
        project,
        title=SETUP_TITLE.format(name=project.name),
        goal=SETUP_GOAL,
        acceptance_criteria=SETUP_CRITERIA,
        complexity=Complexity.MEDIUM,
        kind=CardKind.SETUP,
        ready=True,
    )


# Falling back to plain PostgreSQL -------------------------------------------------------------

FALLBACK_NOTE = "PRAT_DATABASE_FALLBACK"


def fallback_database(settings: Settings, slug: str) -> str | None:
    """Create the project's database on the fallback PostgreSQL server, if one is set, and
    return its address (not recorded yet). None without a fallback server."""
    if settings.fallback_database_url is None:
        return None
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    server = make_url(settings.fallback_database_url.get_secret_value())
    name = database_name(slug)
    engine = create_engine(server, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            if (
                connection.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
                ).first()
                is None
            ):
                connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()
    return server.set(database=name).render_as_string(hide_password=False)


def switch_to_fallback(settings: Settings, slug: str, address: str, reason: str) -> None:
    """Point the project's database variable at plain PostgreSQL for good, and say why."""
    set_variable(settings.home, slug, DATABASE_VARIABLE, address)
    set_variable(settings.home, slug, FALLBACK_NOTE, reason[:300])


def fallback_gate(failed: Sequence[GateResult]) -> GateResult:
    """The switch to PostgreSQL, reported next to the gates so the reviewer and you see it."""
    first = failed[0]
    tail = first.output_tail.strip().splitlines()[-1:] if first.output_tail.strip() else []
    return GateResult(
        name="database-fallback",
        command="run the gates again on plain PostgreSQL",
        exit_code=0,
        passed=True,
        timed_out=False,
        cancelled=False,
        duration_seconds=0.0,
        output_tail=(
            f"On Doltgres the {first.name} gate failed"
            + (f" ({tail[0][:200]})" if tail else "")
            + ". On plain PostgreSQL every gate passed, so the project now uses PostgreSQL: each "
            "card gets its own copy of the database, without a data diff at review or a data "
            "merge on approve."
        ),
        log_path="",
    )
