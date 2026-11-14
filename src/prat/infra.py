"""Infra: checking that a project's environments work, and repairing them when they do not.

An environment check (``prat env check``, or Check environments on the project page) proves the
committed project file on this machine the way a fresh preview would use it: a detached checkout
of the default branch in PRAT's clone, the project's setup command, then every environment
started on free ports, each after the ones whose ports it uses (``{port:name}``), its health URL
and every ``checks`` URL asked while all of them are up. Environments running from the person's
checkout are reported as they stand. Everything the check started is stopped and its checkout
removed.

When something fails, the check files an infra card (kind ``infra``) with the report, unless
one is already open. Its agent is, with the setup agent, the only agent allowed to write
``.prat/``: it diagnoses from the report, fixes the project file, the setup script or a small
thing in the code that keeps an environment from running, and its verify step runs the gates and
the whole environment check again. Nothing lands without the person's approval, like any card.
"""

from __future__ import annotations

import contextlib
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prat.config import Settings
from prat.db import Database
from prat.domain import Autonomy, CardKind, CardStatus, CommentAuthor, Complexity
from prat.environments import (
    STATE_DIR,
    EnvironmentError,
    EnvironmentManager,
    checkout_environment_variables,
    committed_spec,
    load_local,
    merged,
    prepare_preview,
    remove_preview,
    set_up_checkout,
)
from prat.projects import Environment
from prat.setup_agent import PROJECT_FILE_FORMAT, EnvironmentCheck, check_environments

# Environments started by a check are kept apart from the person's (``None``) and from every
# card's preview (cards start at 1).
CHECK_INSTANCE = 0
OPEN = (CardStatus.DRAFT, CardStatus.READY, CardStatus.RUNNING, CardStatus.REVIEW,
        CardStatus.REWORK, CardStatus.BLOCKED)  # fmt: skip

INFRA_TITLE = "Fix {name}'s environments"
INFRA_GOAL = """\
PRAT's environment check found environments of this project that do not work on this machine,
or that nothing proves work together. Find out why from the report below and the code, and fix
it, so that every environment starts from a fresh checkout after the setup command, finds the
environments it depends on through their ports, and answers its health URL and every check URL.

{report}"""
INFRA_CRITERIA = """\
- Every gate passes, setup first, in a fresh worktree.
- Every [[environment]] starts on a free port PRAT picks and its health URL answers.
- An environment that talks to another one finds it through {port:name}, never through a fixed
  port or a default it happens to share with something else on this machine.
- Each environment that serves the app to a person has checks URLs that prove it can reach what
  it depends on (for a dev server that proxies an API: an API URL through the dev server).
- Nothing secret or machine-specific is committed, and the notes say what changed and why."""

INFRA_ROLE = """\
You are PRAT's infra agent. PRAT's environment check found that this project's environments do
not all work: the report is in the card below. You work in a git worktree of the project created
for this card and, with the setup agent, you are the only agent allowed to write .prat/.

Diagnose first: read the report, .prat/project.toml, .prat/setup.sh and the code that starts
each environment (how it reads its port, what it connects to, which variables it reads). Then fix
the cause, in this order of preference: the project file (commands, environments, env, checks),
the setup script, and only when the project cannot run without it, a small change to the code
(say why in your summary). Do not paper over a failure: a check that is removed or loosened so it
passes is not a fix.

How environments fit together: PRAT starts each environment on a free port and fills {port};
{port:api} is the port the "api" environment runs on, and PRAT starts that one first. A dev
server that proxies to an API must get the API's port that way (for example env = { API_PORT =
"{port:api}" }), never a fixed port. Give each environment that a person uses checks URLs that
prove it reaches what it depends on, for example checks = ["http://127.0.0.1:{port}/api/health"].

Prove your fix the way PRAT will: run the setup script and the gates, then start the
environments in the order above and ask their health and check URLs, in one plain command that
stops them there (each tool call is a new shell), for example
`api-command > api.log 2>&1 & sleep 3; API_PORT=PORT web-command > web.log 2>&1 & sleep 3;
curl -s http://127.0.0.1:WEBPORT/api/health; kill %1 %2`. PRAT's policy allows curl to
127.0.0.1 and kill of that command's own jobs only, and refuses files outside the worktree such
as /tmp. PRAT's verify step then runs the whole environment check itself. Delete the logs before you
commit: commit only the fix. Commit your work."""


def infra_system_prompt(
    *,
    name: str,
    default_branch: str,
    autonomy: Autonomy,
    database: bool,
    repo_instructions: str | None = None,
) -> str:
    facts = [
        f"# Project: {name}",
        f"Default branch: {default_branch}.",
        f"Autonomy level for this run: {autonomy}. You may run local programs and scripts; "
        "downloading packages directly is denied, so installs belong in .prat/setup.sh and run "
        "with `sh .prat/setup.sh`.",
    ]
    if database:
        facts.append(
            "The project's database address is in the variable PRAT_DATABASE_URL, and PRAT points "
            "the app's own database variable at this card's copy or branch of it. Use those; "
            "never write an address into a file."
        )
    parts = [INFRA_ROLE]
    if repo_instructions:
        parts.append(repo_instructions)
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
                "- Change .prat/ first; change other files only when the project cannot run "
                "without it, and say why in your summary.",
                "- Never point the app at the person's own databases or at fixed ports another "
                "program on this machine may use.",
                "- If PRAT's policy denies a call, do not try to get around it.",
                "- Finish with a short summary: what was broken, why, what you changed and what "
                "you checked.",
            ]
        ),
        "# If you are blocked\nIf the fix needs something only a person can provide (a "
        "licence, an account, a runtime that is not installed), make the last line of your "
        "final message:\n\nBLOCKED: <one specific question>",
    ]
    return "\n\n".join(parts) + "\n"


INFRA_TASK = "Repair this project's environments as described in your instructions."


# The check ---------------------------------------------------------------------------------


@dataclass
class CheckoutState:
    name: str
    state: str
    message: str = ""


@dataclass
class InfraReport:
    slug: str
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    problem: str | None = None
    setup_problem: str | None = None
    checks: list[EnvironmentCheck] = field(default_factory=list)
    checkout: list[CheckoutState] = field(default_factory=list)
    # What the project file leaves unproven, even when every check passes.
    gaps: list[str] = field(default_factory=list)
    card_id: int | None = None

    @property
    def passed(self) -> bool:
        return (
            self.problem is None
            and self.setup_problem is None
            and not self.gaps
            and all(check.passed for check in self.checks)
        )

    @property
    def failures(self) -> list[EnvironmentCheck]:
        return [check for check in self.checks if not check.passed]

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "passed": self.passed}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InfraReport:
        return cls(
            slug=str(data.get("slug", "")),
            checked_at=str(data.get("checked_at", "")),
            problem=data.get("problem"),
            setup_problem=data.get("setup_problem"),
            checks=[EnvironmentCheck(**c) for c in data.get("checks", [])],
            checkout=[CheckoutState(**c) for c in data.get("checkout", [])],
            gaps=[str(g) for g in data.get("gaps", [])],
            card_id=data.get("card_id"),
        )

    def render(self) -> str:
        """The report as Markdown, for the card and the terminal."""
        lines = ["## Environment check", ""]
        if self.problem:
            lines += [f"The check could not run: {self.problem}", ""]
        if self.setup_problem:
            lines += [f"Setup in a fresh checkout failed: {self.setup_problem}", ""]
        for check in self.checks:
            mark = "works" if check.passed else "FAILED"
            port = f" (port {check.port})" if check.port else ""
            lines.append(f"- **{check.name}**{port}: {mark}. {check.detail}")
            if check.log_tail:
                lines += ["", "  ```", *("  " + line for line in check.log_tail.splitlines()[-20:])]
                lines += ["  ```"]
        if self.gaps:
            lines += ["", "Not proven by the project file:"]
            lines += [f"- {gap}" for gap in self.gaps]
        broken = [s for s in self.checkout if s.state in ("unhealthy", "exited")]
        if broken:
            lines += ["", "Running from the person's checkout:"]
            lines += [f"- **{s.name}**: {s.state}. {s.message}".rstrip() for s in broken]
        if self.passed:
            lines += ["", "Every environment works."]
        return "\n".join(lines).rstrip() + "\n"


def gaps(envs: list[Environment]) -> list[str]:
    """What a set of environments leaves unproven. Several environments that never name each
    other's ports and declare no checks are only checked one by one: a dev server that proxies
    an API on a default port answers its own health URL while every API call fails."""
    if len(envs) < 2 or any(env.needs or env.checks for env in envs):
        return []
    names = ", ".join(env.name for env in envs)
    return [
        f"{names} run side by side, but none uses another's port ({{port:name}}) or has checks "
        "URLs, so nothing proves they reach each other; each is only checked on its own"
    ]


def check_folder(home: Path, slug: str) -> Path:
    return home / STATE_DIR / "checks" / slug


def report_path(home: Path, slug: str) -> Path:
    return home / STATE_DIR / "checks" / f"{slug}.json"


def last_report(home: Path, slug: str) -> InfraReport | None:
    try:
        return InfraReport.from_dict(
            json.loads(report_path(home, slug).read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, TypeError):
        return None


def _save(home: Path, report: InfraReport) -> None:
    path = report_path(home, report.slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict()), encoding="utf-8")


_RUNNING: set[str] = set()
_RUNNING_LOCK = threading.Lock()


def is_checking(slug: str) -> bool:
    with _RUNNING_LOCK:
        return slug in _RUNNING


def run_check(
    settings: Settings,
    db: Database,
    manager: EnvironmentManager,
    slug: str,
    *,
    fix: bool = True,
    stop: threading.Event | None = None,
) -> InfraReport:
    """Check every environment of a project as described in the module docstring, save the
    report, and with ``fix`` file an infra card when something failed. One check per project
    at a time; raises ``EnvironmentError`` if one is already running. Raises ``LookupError``
    for an unknown project."""
    with _RUNNING_LOCK:
        if slug in _RUNNING:
            raise EnvironmentError(f"an environment check of {slug} is already running")
        _RUNNING.add(slug)
    try:
        report = _check(settings, db, manager, slug, stop)
        if fix and not report.passed and report.problem is None:
            report.card_id = file_card(db, slug, report)
        _save(settings.home, report)
        return report
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(slug)


def _check(
    settings: Settings,
    db: Database,
    manager: EnvironmentManager,
    slug: str,
    stop: threading.Event | None,
) -> InfraReport:
    from prat import store

    report = InfraReport(slug)
    _checkout, spec, problem = committed_spec(db, slug)
    with db.session() as session:
        project = store.get_project(session, slug)
        agent, branch = Path(project.agent_path), project.default_branch
    if problem is not None:
        report.problem = problem
        return report
    local = load_local(settings.home, slug)
    envs = merged(list(spec.environment), local)
    if not envs:
        report.problem = "the project file declares no environments"
        return report
    report.gaps = gaps(list(spec.environment))
    for env in envs:
        view = manager.view(slug, env)
        report.checkout.append(CheckoutState(env.name, view.state, view.message))
    if not (agent / ".git").exists():
        report.problem = "PRAT's clone of the project does not exist yet; run a card first"
        return report
    variables = checkout_environment_variables(db, settings.home, slug)
    folder = check_folder(settings.home, slug)
    try:
        prepare_preview(agent, branch, folder)
    except EnvironmentError as exc:
        report.problem = str(exc)
        return report
    try:
        sandbox = (
            manager.sandboxes(slug, CHECK_INSTANCE, folder, variables)
            if manager.sandboxes
            else None
        )
        report.setup_problem = set_up_checkout(spec, folder, variables, sandbox=sandbox, stop=stop)
        if report.setup_problem is None:
            report.checks = check_environments(
                manager,
                slug,
                spec,
                folder,
                CHECK_INSTANCE,
                variables,
                stop=stop.is_set if stop is not None else lambda: False,
                envs=envs,
            )
    finally:
        with contextlib.suppress(OSError):
            remove_preview(agent, folder)
    return report


def file_card(db: Database, slug: str, report: InfraReport) -> int:
    """The id of the project's open infra card, or of a new one carrying the report."""
    from prat import store

    with db.session() as session:
        project = store.get_project(session, slug)
        for card in store.list_cards(session, project):
            if card.kind == CardKind.INFRA and card.status in OPEN:
                store.add_comment(
                    session, card, report.render(), author=CommentAuthor.SYSTEM, kind="infra"
                )
                return int(card.id)
        card = store.add_card(
            session,
            project,
            title=INFRA_TITLE.format(name=project.name),
            goal=INFRA_GOAL.format(report=report.render()),
            acceptance_criteria=INFRA_CRITERIA,
            complexity=Complexity.MEDIUM,
            kind=CardKind.INFRA,
            ready=True,
        )
        return int(card.id)
