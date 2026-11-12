"""The staged trial: proof that a candidate works as a running system.

The trial harness is code of the running PRAT; the candidate is only ever its
subject. Nothing here touches the live database, the live home folder or the
live ports. Steps, each recorded with its output:

1. ``checkout``  a detached worktree of the self clone at the candidate commit.
2. ``install``   an environment for the candidate. Unchanged dependencies: the
   running interpreter with the candidate's ``src`` first on the path (no
   network, no new executables). Changed dependencies: a fresh virtual
   environment with a full install; no network postpones the trial.
3. ``selfcheck`` in the candidate environment: import every module, compile
   every template, load settings.
4. ``data``      a backup copy of the live database (SQLite online backup) in a
   temporary home, the candidate's migrations run on it, an integrity check,
   every table read; then the running version reads the migrated copy too.
5. ``boot``      the candidate's server on a free loopback port with the
   temporary home and a scrubbed environment; it must answer ``/healthz`` with
   the candidate commit.
6. ``probe``     the running PRAT inspects it over HTTP: no session is refused,
   no CSRF token and a foreign Origin are refused, sign-in works, the main
   pages render, and a scripted card in a throwaway repository reaches review
   with its push attempt and its protected-path write both denied; approve
   merges it.
7. ``teardown``  the candidate's whole process tree is stopped and the trial
   folder deleted; the logs are kept.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, NoReturn, TextIO

import httpx
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from prat.config import Settings, load_settings
from prat.db import Card, Database, Run
from prat.db.models import Base
from prat.git import Git
from prat.git.worktree import rmtree
from prat.proc import ProcessTree, run_tree, spawn

# Long enough for the project's full gates (PRAT's own suite takes minutes).
TRIAL_TIMEOUT_SECONDS = 5400.0
STEP_TIMEOUTS = {
    "install": 900.0,
    "selfcheck": 300.0,
    "invariants": 900.0,
    "migrate": 300.0,
    "boot": 90.0,
    "card": 180.0,
}
OUTPUT_TAIL_CHARS = 4000
PROBE_SLUG = "trial-probe"
PROBE_TITLE = "Trial probe card"
PROTECTED_PROBE_PATH = ".prat/project.toml"
DEPENDENCY_FILES = re.compile(r"(^|/)(requirements[^/]*\.(txt|in)|[^/]*\.lock)$")
# Signs that the package index could not be reached. "No matching distribution"
# alone means the candidate asked for something that does not exist: a failure.
_NETWORK_ERRORS = re.compile(
    r"(getaddrinfo|NewConnectionError|Temporary failure in name resolution|"
    r"Network is unreachable|Connection refused|ConnectTimeout|ProxyError|"
    r"Failed to establish a new connection)",
    re.IGNORECASE,
)
_CSRF_META = re.compile(r'<meta name="csrf-token" content="([^"]*)">')

# Runs a module or a script with the candidate's source first on the path, and
# refuses to run if ``prat`` does not come from there. Passed to ``python -I -c``.
BOOT = """\
import runpy, sys
from pathlib import Path
src, target = sys.argv[1], sys.argv[2]
if src:
    sys.path.insert(0, src)
sys.argv = [target, *sys.argv[3:]]
import prat
if src and not Path(prat.__file__).resolve().is_relative_to(Path(src).resolve()):
    print("PRAT was not imported from the candidate:", prat.__file__, file=sys.stderr)
    raise SystemExit(97)
if target.endswith(".py"):
    runpy.run_path(target, run_name="__main__")
else:
    runpy.run_module(target, run_name="__main__", alter_sys=True)
"""

SELFCHECK = """\
import importlib, pkgutil, sys, tempfile
import prat
failures = []
for info in pkgutil.walk_packages(prat.__path__, "prat."):
    if ".alembic." in info.name or info.name.endswith("__main__"):
        continue
    try:
        importlib.import_module(info.name)
    except Exception as exc:
        failures.append(f"{info.name}: {type(exc).__name__}: {exc}")
from prat.web.app import make_templates
env = make_templates().env
for name in env.list_templates():
    try:
        env.get_template(name)
    except Exception as exc:
        failures.append(f"template {name}: {type(exc).__name__}: {exc}")
from prat.config import load_settings
load_settings(tempfile.mkdtemp())
for line in failures:
    print(line, file=sys.stderr)
print(f"selfcheck: {len(failures)} problem(s)")
raise SystemExit(1 if failures else 0)
"""

_ONE_AT_A_TIME = threading.Lock()


class TrialStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    POSTPONED = "postponed"


@dataclass
class TrialStep:
    name: str
    ok: bool
    message: str
    seconds: float = 0.0
    output: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "message": self.message,
            "seconds": round(self.seconds, 2),
            "output": self.output[-OUTPUT_TAIL_CHARS:],
        }


@dataclass
class TrialResult:
    commit: str
    status: TrialStatus
    steps: list[TrialStep] = field(default_factory=list)
    log_dir: str | None = None

    @property
    def passed(self) -> bool:
        return self.status is TrialStatus.PASSED

    @property
    def failed_step(self) -> TrialStep | None:
        return next((step for step in self.steps if not step.ok), None)

    @property
    def summary(self) -> str:
        step = self.failed_step
        if self.status is TrialStatus.PASSED:
            return f"trial of {self.commit[:10]} passed"
        what = f"{step.name}: {step.message}" if step else "no step ran"
        return f"trial of {self.commit[:10]} {self.status} at {what}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "status": str(self.status),
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
            "log_dir": self.log_dir,
        }


class _Stop(Exception):
    def __init__(self, status: TrialStatus = TrialStatus.FAILED) -> None:
        self.status = status


@dataclass
class CandidateEnv:
    """How to run code in the candidate's environment."""

    python: Path
    src: Path | None  # put first on the path; None when installed into ``python``

    def argv(self, target: str, *args: str) -> list[str]:
        return [
            str(self.python),
            "-I",
            "-X",
            "utf8",
            "-c",
            BOOT,
            str(self.src or ""),
            target,
            *args,
        ]


def child_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for candidate processes: no PRAT settings, no keys."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PRAT_", "ANTHROPIC_", "CLAUDE_", "PYTHON", "PYTEST_"))
    }
    env.update(extra or {})
    return env


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def index_reachable(timeout: float = 8.0) -> bool:
    """Whether the package index pip would use answers at all."""
    url = os.environ.get("PIP_INDEX_URL") or "https://pypi.org/simple/"
    try:
        httpx.get(url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError:
        return False
    return True


def dependencies_changed(clone: Path, base: str, candidate: str) -> bool:
    """Whether the candidate's dependencies differ from the trusted version's."""
    git = Git(clone, timeout=120)
    changed = git.run("diff", "--name-only", base, candidate).stdout.splitlines()
    if any(DEPENDENCY_FILES.search(name) for name in changed):
        return True
    if "pyproject.toml" not in changed:
        return False

    def deps(ref: str) -> Any:
        shown = git.run("show", f"{ref}:pyproject.toml", check=False)
        if not shown.ok:
            return None
        data = tomllib.loads(shown.stdout)
        project = data.get("project", {})
        return (
            project.get("dependencies"),
            project.get("optional-dependencies"),
            project.get("requires-python"),
            data.get("build-system"),
        )

    return bool(deps(base) != deps(candidate))


# The trial ----------------------------------------------------------------------------


class Trial:
    def __init__(
        self,
        settings: Settings,
        *,
        clone: Path,
        base: str,
        candidate: str,
        root: Path | None = None,
        log_dir: Path | None = None,
        timeout: float = TRIAL_TIMEOUT_SECONDS,
        python: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.clone = clone
        self.base = base
        self.candidate = candidate
        self.root = root or settings.home / "self" / "trials" / candidate[:12]
        self.log_dir = log_dir or settings.home / "self" / "trial-logs" / candidate[:12]
        self.python = python or Path(sys.executable)
        self.clock = clock
        self.deadline = clock() + timeout
        self.result = TrialResult(candidate, TrialStatus.FAILED, log_dir=str(self.log_dir))
        self.server: ProcessTree | None = None
        self._log: TextIO | None = None
        self.port = 0
        self.env: CandidateEnv | None = None

    # Plumbing ---------------------------------------------------------------------------

    @property
    def src(self) -> Path:
        return self.root / "src"

    @property
    def home(self) -> Path:
        return self.root / "home"

    def _left(self, name: str) -> float:
        left = self.deadline - self.clock()
        if left <= 0:
            self._record(name, False, "the trial ran out of time")
            raise _Stop()
        return min(left, STEP_TIMEOUTS.get(name, left))

    def _record(
        self, name: str, ok: bool, message: str, started: float = 0.0, output: str = ""
    ) -> None:
        seconds = self.clock() - started if started else 0.0
        self.result.steps.append(TrialStep(name, ok, message, seconds, output))

    def _fail(self, name: str, message: str, started: float, output: str = "") -> NoReturn:
        self._record(name, False, message, started, output)
        raise _Stop()

    def _candidate(self, name: str) -> CandidateEnv:
        if self.env is None:
            self._fail(name, "the candidate environment was not prepared", self.clock())
        return self.env

    def _run(
        self, name: str, argv: Sequence[str], *, timeout_key: str, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        started = self.clock()
        timeout = self._left(timeout_key)
        try:
            return run_tree(argv, timeout=timeout, **kwargs)
        except subprocess.TimeoutExpired:
            self._fail(name, f"timed out after {timeout:.0f}s", started)
        except OSError as exc:
            self._fail(name, f"could not start: {exc}", started)

    # Steps ------------------------------------------------------------------------------

    def checkout(self) -> None:
        started = self.clock()
        if self.root.exists():
            self._remove_worktree()
            rmtree(self.root)
        self.root.mkdir(parents=True)
        git = Git(self.clone, timeout=self._left("checkout"))
        added = git.run(
            "worktree", "add", "--detach", "--force", str(self.src), self.candidate, check=False
        )
        if not added.ok:
            self._fail("checkout", "could not check out the candidate", started, added.stderr)
        head = Git(self.src).head()
        if head != self.candidate:
            self._fail("checkout", f"checked out {head[:10]}, not the candidate", started)
        self._record("checkout", True, f"checked out {self.candidate[:10]}", started)

    def install(self) -> None:
        started = self.clock()
        if not dependencies_changed(self.clone, self.base, self.candidate):
            self.env = env = CandidateEnv(self.python, self.src / "src")
            probe = self._run(
                "install",
                env.argv("prat", "--help"),
                timeout_key="install",
                cwd=self.root,
                env=child_env(),
            )
            if probe.returncode != 0:
                self._fail(
                    "install", "the candidate does not start", started, probe.stdout + probe.stderr
                )
            self._record(
                "install", True, "dependencies unchanged: reused the running environment", started
            )
            return
        if not index_reachable():
            self._record(
                "install",
                False,
                "dependencies changed and the package index is unreachable",
                started,
            )
            raise _Stop(TrialStatus.POSTPONED)
        venv = self.root / "venv"
        made = self._run(
            "install",
            [str(self.python), "-m", "venv", str(venv)],
            timeout_key="install",
            env=child_env(),
        )
        if made.returncode != 0:
            self._fail("install", "could not create a virtual environment", started, made.stderr)
        python = (
            venv
            / ("Scripts" if os.name == "nt" else "bin")
            / ("python.exe" if os.name == "nt" else "python")
        )
        pip = self._run(
            "install",
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-input",
                "--disable-pip-version-check",
                f"{self.src}[dev]",
            ],
            timeout_key="install",
            env=child_env(),
        )
        output = pip.stdout + pip.stderr
        if pip.returncode != 0:
            if _NETWORK_ERRORS.search(output):
                self._record(
                    "install",
                    False,
                    "dependencies changed and the network is unavailable",
                    started,
                    output,
                )
                raise _Stop(TrialStatus.POSTPONED)
            self._fail("install", "the candidate's dependencies did not install", started, output)
        self.env = CandidateEnv(python, None)
        self._record(
            "install", True, "dependencies changed: installed into a new environment", started
        )

    def selfcheck(self) -> None:
        started = self.clock()
        env = self._candidate("selfcheck")
        script = self.root / "selfcheck.py"
        script.write_text(SELFCHECK, encoding="utf-8")
        done = self._run(
            "selfcheck",
            env.argv(str(script)),
            timeout_key="selfcheck",
            cwd=self.root,
            env=child_env(),
        )
        output = done.stdout + done.stderr
        if done.returncode != 0:
            self._fail(
                "selfcheck", "modules, templates or settings failed to load", started, output
            )
        self._record(
            "selfcheck", True, "every module imports and every template compiles", started, output
        )

    def invariants(self) -> None:
        """The invariant suite as it was before the change, against the candidate."""
        from prat.improve.invariants import export_trusted, run_invariants

        started = self.clock()
        env = self._candidate("invariants")
        files = export_trusted(self.clone, self.base, self.root / "invariants")
        if not any(name.startswith("test_") for name in files):
            self._record("invariants", True, "the trusted version has no invariant suite", started)
            return
        result = run_invariants(
            self.root / "invariants",
            launcher=env.argv,
            candidate_root=self.src,
            env=child_env(),
            timeout=self._left("invariants"),
            log_path=self.log_dir / "invariants.log",
        )
        if not result.passed:
            self._fail("invariants", result.summary, started, result.output_tail)
        self._record("invariants", True, result.summary, started)

    def gates(self) -> None:
        """The project's gates, as the trusted version defines them, on the candidate."""
        from prat.projects import ProjectFileError, parse_spec, show_project_file
        from prat.verification import run_gates

        started = self.clock()
        shown = show_project_file(Git(self.clone), self.base)
        if not shown.ok:
            self._fail("gates", "the trusted version has no project file", started)
        try:
            spec = parse_spec(shown.stdout)
        except ProjectFileError as exc:
            self._fail("gates", f"the trusted project file is invalid: {exc}", started)
        timeout = min(float(spec.verify.timeout_seconds), self._left("gates"))
        results = run_gates(
            spec, self.src, self.root / "gates", stop=threading.Event(), timeout=timeout
        )
        failed = [gate for gate in results if not gate.passed]
        if failed:
            self._fail("gates", failed[0].summary, started, failed[0].output_tail)
        names = ", ".join(gate.name for gate in results) or "none configured"
        self._record("gates", True, f"gates passed on the candidate: {names}", started)

    def data(self, live: Database) -> None:
        started = self.clock()
        env = self._candidate("data")
        self.home.mkdir(parents=True, exist_ok=True)
        copy = self.home / "prat.db"
        source = make_url(live.engine.url)
        if source.get_backend_name() == "sqlite" and source.database:
            with (
                contextlib.closing(sqlite3.connect(source.database)) as src,
                contextlib.closing(sqlite3.connect(copy)) as dst,
            ):
                src.backup(dst)
            how = "copied the live database"
        else:
            how = "started from an empty database (the live one is not SQLite)"
        migrate = self._run(
            "data",
            env.argv("prat", "--home", str(self.home), "db", "upgrade"),
            timeout_key="migrate",
            cwd=self.root,
            env=child_env(),
        )
        if migrate.returncode != 0:
            self._fail(
                "data",
                "the candidate's migrations failed",
                started,
                migrate.stdout + migrate.stderr,
            )
        with contextlib.closing(sqlite3.connect(copy)) as conn, conn:
            check = conn.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                self._fail("data", f"integrity check failed: {check}", started)
            tables = [
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            ]
            for table in tables:
                # Names come from sqlite_master, not from input.
                conn.execute(f'SELECT * FROM "{table}"').fetchall()  # noqa: S608
            # The trial must never run self-improvement against live repositories.
            conn.execute("DELETE FROM app_settings WHERE key = 'improve.enabled'")
            conn.execute(
                "INSERT INTO app_settings (key, value, updated_at) "
                "VALUES ('improve.enabled', 'false', CURRENT_TIMESTAMP)"
            )
        trial_db = Database(f"sqlite:///{copy.as_posix()}")
        try:
            with trial_db.session() as session:
                for mapper in Base.registry.mappers:
                    session.execute(select(mapper.class_).limit(50)).all()
                session.execute(text("SELECT 1"))
        except Exception as exc:
            self._fail(
                "data",
                "the running version cannot read the migrated database; split this change into an "
                "additive step and a later removal",
                started,
                f"{type(exc).__name__}: {exc}",
            )
        finally:
            trial_db.dispose()
        self._record(
            "data", True, f"{how}, migrated it and read {len(tables)} tables both ways", started
        )

    def boot(self) -> None:
        started = self.clock()
        env = self._candidate("boot")
        self.port = free_port()
        script = self.root / "probe-script.json"
        script.write_text(json.dumps(PROBE_SCRIPT), encoding="utf-8")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Closed in teardown, after the server has stopped writing to it.
        self._log = log = (self.log_dir / "server.log").open("w", encoding="utf-8")
        argv = env.argv(
            "prat",
            "--home",
            str(self.home),
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--runtime",
            "fake",
            "--script",
            str(script),
        )
        try:
            self.server = server = spawn(
                argv,
                cwd=self.root,
                env=child_env({"PRAT_BUILD_COMMIT": self.candidate}),
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exc:
            self._fail("boot", f"could not start the server: {exc}", started)
        limit = self.clock() + self._left("boot")
        last = ""
        while self.clock() < limit:
            if server.proc.poll() is not None:
                log.flush()
                self._fail("boot", "the server exited", started, self._server_log())
            try:
                response = httpx.get(f"{self.base_url}/healthz", timeout=2.0)
                body = response.json() if response.status_code == 200 else {}
                if body.get("commit") == self.candidate:
                    self._record("boot", True, f"answering on port {self.port}", started)
                    return
                last = f"health answered {response.status_code}: {response.text[:200]}"
            except (httpx.HTTPError, ValueError) as exc:
                last = f"{type(exc).__name__}"
            time.sleep(0.25)
        self._fail("boot", f"no healthy answer in time ({last})", started, self._server_log())

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _server_log(self) -> str:
        path = self.log_dir / "server.log"
        return (
            path.read_text(encoding="utf-8", errors="replace")[-OUTPUT_TAIL_CHARS:]
            if path.exists()
            else ""
        )

    def probe(self) -> None:
        started = self.clock()
        origin = self.base_url
        with httpx.Client(base_url=origin, timeout=15.0, follow_redirects=False) as client:
            for path in ("/board", "/projects", "/settings", "/cards/new"):
                response = client.get(path)
                if response.status_code not in (303, 401) or (
                    response.status_code == 303
                    and not response.headers.get("location", "").startswith("/login")
                ):
                    self._fail(
                        "probe",
                        f"{path} answered {response.status_code} without a session",
                        started,
                    )
            refused = client.post("/cards", data={"title": "x"}, headers={"Origin": origin})
            if refused.status_code not in (401, 403):
                self._fail("probe", f"a post without a session got {refused.status_code}", started)

            token_file = load_settings(self.home).access_token_file
            if not token_file.exists():
                self._fail("probe", "the candidate wrote no access token", started)
            signed = client.post(
                "/login",
                data={"token": token_file.read_text(encoding="utf-8").strip()},
                headers={"Origin": origin},
            )
            if signed.status_code != 303:
                self._fail(
                    "probe", f"sign-in with the access token got {signed.status_code}", started
                )
            for path in ("/board", "/projects", "/settings", "/cards/new", "/projects/new"):
                page = client.get(path)
                if page.status_code != 200:
                    self._fail(
                        "probe",
                        f"{path} answered {page.status_code} when signed in",
                        started,
                        page.text[-2000:],
                    )
            match = _CSRF_META.search(client.get("/board").text)
            if not match or not match.group(1):
                self._fail("probe", "no CSRF token on the board page", started)
            csrf = match.group(1)
            for headers, what in (
                ({"Origin": origin}, "no CSRF token"),
                ({"Origin": origin, "X-CSRF-Token": "wrong"}, "a wrong CSRF token"),
                ({"Origin": "http://evil.example", "X-CSRF-Token": csrf}, "a foreign Origin"),
            ):
                response = client.post("/cards", data={"title": "x"}, headers=headers)
                if response.status_code != 403:
                    self._fail("probe", f"a post with {what} got {response.status_code}", started)
            good = {"Origin": origin, "X-CSRF-Token": csrf}
            self._probe_card(client, good, started)
        self._record(
            "probe", True, "sessions, CSRF, Origin, pages, policy and approve all held", started
        )

    def _probe_card(self, client: httpx.Client, headers: dict[str, str], started: float) -> None:
        repo = make_probe_repo(self.root / "probe-repos", self.python)
        agent = self.root / "probe-repos" / "probe-agent"
        added = client.post(
            "/projects",
            data={"checkout": str(repo), "slug": PROBE_SLUG, "agent_path": str(agent)},
            headers=headers,
        )
        if added.status_code != 303:
            self._fail(
                "probe",
                f"adding the probe project got {added.status_code}",
                started,
                added.text[-2000:],
            )
        created = client.post(
            "/cards",
            data={"project": PROBE_SLUG, "title": PROBE_TITLE, "goal": "probe", "start": "1"},
            headers=headers,
        )
        if created.status_code != 303:
            self._fail(
                "probe",
                f"creating the probe card got {created.status_code}",
                started,
                created.text[-2000:],
            )
        db = Database(f"sqlite:///{(self.home / 'prat.db').as_posix()}")
        try:
            card_id, status, run = self._wait_for_card(db, started)
            # The policy checks come first: they name the real problem even when a
            # later line of defence (verification) also stopped the card.
            denied = self._denials(run)
            if not any("push" in item for item in denied):
                self._fail(
                    "probe", "the scripted git push was not denied", started, "\n".join(denied)
                )
            if not any(PROTECTED_PROBE_PATH in item for item in denied):
                self._fail(
                    "probe",
                    "the write to a protected path was not denied",
                    started,
                    "\n".join(denied),
                )
            if status != "review":
                self._fail(
                    "probe",
                    f"the probe card ended {status}, not review",
                    started,
                    self._server_log(),
                )
            approved = client.post(f"/cards/{card_id}/approve", headers=headers)
            if approved.status_code != 303:
                self._fail(
                    "probe", f"approve got {approved.status_code}", started, approved.text[-2000:]
                )
            merged = Git(agent).run("show", "main:probe.txt", check=False)
            if not merged.ok:
                self._fail("probe", "approve did not merge the probe card", started, merged.stderr)
            original = Git(repo).run("show", f"HEAD:{PROTECTED_PROBE_PATH}").stdout
            on_main = Git(agent).run("show", f"main:{PROTECTED_PROBE_PATH}").stdout
            if on_main != original:
                self._fail("probe", "the protected file changed", started)
        finally:
            db.dispose()

    def _wait_for_card(self, db: Database, started: float) -> tuple[int, str, dict[str, Any]]:
        limit = self.clock() + self._left("card")
        while self.clock() < limit:
            with db.session() as session:
                card = session.scalars(select(Card).where(Card.title == PROBE_TITLE)).first()
                if card is not None and str(card.status) not in ("draft", "ready", "running"):
                    run = session.scalars(
                        select(Run).where(Run.card_id == card.id).order_by(Run.id.desc())
                    ).first()
                    journal = (run.journal_path if run else None) or ""
                    return (
                        card.id,
                        str(card.status),
                        {"id": run.id if run else 0, "journal": journal},
                    )
            time.sleep(0.25)
        self._fail("probe", "the probe card did not finish in time", started, self._server_log())

    def _denials(self, run: dict[str, Any]) -> list[str]:
        path = (
            Path(run["journal"])
            if run["journal"]
            else self.home / "journal" / f"run-{run['id']}.jsonl"
        )
        denied: list[str] = []
        if not path.exists():
            return denied
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            data = entry.get("data") or {}
            if entry.get("kind") == "policy" and data.get("allowed") is False:
                denied.append(json.dumps(data.get("call"), sort_keys=True))
        return denied

    def teardown(self) -> None:
        started = self.clock()
        problems: list[str] = []
        if self.server is not None:
            self.server.kill()
            try:
                self.server.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                problems.append("the server did not stop")
            self.server.close()
        if self._log is not None:
            self._log.close()
        self._remove_worktree()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        (self.log_dir / "trial.json").write_text(
            json.dumps(self.result.to_dict(), indent=2), encoding="utf-8"
        )
        try:
            rmtree(self.root)
        except OSError as exc:
            problems.append(f"could not delete the trial folder: {exc}")
        self._record(
            "teardown", not problems, "; ".join(problems) or "stopped and cleaned up", started
        )

    def _remove_worktree(self) -> None:
        git = Git(self.clone, timeout=120)
        git.run("worktree", "remove", "--force", str(self.src), check=False)
        git.run("worktree", "prune", check=False)

    # Driver ------------------------------------------------------------------------------

    def run(
        self, live: Database, *, probes: bool = True, invariants: bool = True, gates: bool = False
    ) -> TrialResult:
        """Cheap checks first, the slow ones (the project's full gates) last."""
        try:
            self.checkout()
            self.install()
            self.selfcheck()
            if invariants:
                self.invariants()
            self.data(live)
            if probes:
                self.boot()
                self.probe()
            if gates:
                self.gates()
            self.result.status = TrialStatus.PASSED
        except _Stop as stop:
            self.result.status = stop.status
        except Exception as exc:
            self._record("trial", False, f"the trial harness failed: {type(exc).__name__}: {exc}")
            self.result.status = TrialStatus.FAILED
        finally:
            self.teardown()
        if self.result.status is TrialStatus.PASSED and not all(s.ok for s in self.result.steps):
            self.result.status = TrialStatus.FAILED
        return self.result


PROBE_SCRIPT: list[dict[str, Any]] = [
    {"op": "write", "path": "probe.txt", "content": "probe\n"},
    {"op": "bash", "command": "git push origin HEAD:main"},
    {"op": "write", "path": PROTECTED_PROBE_PATH, "content": 'name = "taken over"\n'},
    {"op": "finish", "message": "probe done"},
]


def make_probe_repo(root: Path, python: Path) -> Path:
    """A throwaway repository with a passing gate, for the probe card."""
    repo = root / "probe"
    repo.mkdir(parents=True)
    git = Git(repo, timeout=60)
    git.run("init", "-q", "-b", "main")
    git.run("config", "user.name", "PRAT trial")
    git.run("config", "user.email", "trial@prat.invalid")
    git.run("config", "core.autocrlf", "false")
    # The person's global hooks must not decide whether the probe repository commits.
    hooks = repo / ".git" / "prat-hooks"
    hooks.mkdir()
    git.run("config", "core.hooksPath", hooks.resolve().as_posix())
    (repo / "README.md").write_text("# probe\n", encoding="utf-8", newline="\n")
    (repo / ".prat").mkdir()
    (repo / ".prat" / "project.toml").write_text(
        "\n".join(
            [
                'name = "trial-probe"',
                'default_branch = "main"',
                'autonomy = "medium"',
                "",
                "[commands]",
                f'test = [{json.dumps(python.as_posix())}, "-c", "pass"]',
                "",
                "[verify]",
                'gates = ["test"]',
                "timeout_seconds = 60",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    git.run("add", "-A")
    git.run("commit", "-q", "-m", "probe repository")
    return repo


def run_trial(
    settings: Settings,
    live: Database,
    *,
    clone: Path,
    base: str,
    candidate: str,
    probes: bool = True,
    invariants: bool = True,
    gates: bool = False,
    **options: Any,
) -> TrialResult:
    """Run one trial. Trials run one at a time; a second caller is postponed."""
    if not _ONE_AT_A_TIME.acquire(blocking=False):
        result = TrialResult(candidate, TrialStatus.POSTPONED)
        result.steps.append(TrialStep("trial", False, "another trial is running"))
        return result
    try:
        return Trial(settings, clone=clone, base=base, candidate=candidate, **options).run(
            live, probes=probes, invariants=invariants, gates=gates
        )
    finally:
        _ONE_AT_A_TIME.release()


__all__ = [
    "CandidateEnv",
    "TrialResult",
    "TrialStatus",
    "TrialStep",
    "child_env",
    "dependencies_changed",
    "run_trial",
]
