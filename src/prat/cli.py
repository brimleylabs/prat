"""Command line interface."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from sqlalchemy.exc import SQLAlchemyError

from prat import __version__, project_detect, rhythm, store
from prat.config import DEFAULT_CONFIG_TOML, Settings, load_settings
from prat.db import Database
from prat.db.migrate import is_current, upgrade
from prat.domain import Autonomy, CardStatus, CommentAuthor, Complexity
from prat.engine import (
    ApproveError,
    Engine,
    EngineError,
    FakeReviewer,
    FakeSecurityReviewer,
    NoPlanner,
    Planner,
    Reviewer,
    RunResult,
    SecurityReviewer,
)
from prat.git import CloneError, GitError
from prat.journal import read_journal
from prat.knowledge.entries import Entry, KnowledgeBase
from prat.model_registry import Role, resolve_model
from prat.overlay import OverlayGuidedRuntime
from prat.projects import (
    PROJECT_FILE_REL,
    Environment,
    ProjectFileError,
    default_agent_path,
    load_spec,
    slugify,
)
from prat.runtime import AgentRuntime, FakeRuntime, FakeScript

if TYPE_CHECKING:
    from prat.environments import EnvironmentManager

app = typer.Typer(no_args_is_help=True, add_completion=False, help="PRAT: one agent per card.")
db_app = typer.Typer(no_args_is_help=True, help="Database management.")
project_app = typer.Typer(no_args_is_help=True, help="Register and inspect projects.")
card_app = typer.Typer(no_args_is_help=True, help="Create, run, review and merge cards.")
auth_app = typer.Typer(no_args_is_help=True, help="Agent runtime authentication.")
store_app = typer.Typer(no_args_is_help=True, help="PRAT's store: SQLite or Dolt, and its history.")
improve_app = typer.Typer(no_args_is_help=True, help="Self-improvement: PRAT working on itself.")
env_app = typer.Typer(no_args_is_help=True, help="Start, stop and check a project's environments.")
sandbox_app = typer.Typer(
    no_args_is_help=True, help="The sandbox: containers for a card's commands."
)
knowledge_app = typer.Typer(
    no_args_is_help=True, help="What PRAT has learned about each project, and its graph."
)
dream_app = typer.Typer(
    no_args_is_help=True, help="Quiet-hours work: tidying, keeping knowledge true, reflecting."
)
overlay_app = typer.Typer(
    no_args_is_help=True,
    help="This installation's own rules, guidance and settings, kept apart from PRAT itself.",
)
checkpoint_app = typer.Typer(
    no_args_is_help=True, help="Landed cards, with their code and data, and how to take them back."
)
app.add_typer(db_app, name="db")
app.add_typer(checkpoint_app, name="checkpoint")
app.add_typer(project_app, name="project")
app.add_typer(card_app, name="card")
app.add_typer(auth_app, name="auth")
app.add_typer(improve_app, name="improve")
app.add_typer(overlay_app, name="overlay")
app.add_typer(env_app, name="env")
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(knowledge_app, name="knowledge")
app.add_typer(dream_app, name="dream")
app.add_typer(store_app, name="store")

console = Console(soft_wrap=True)
err = Console(stderr=True, soft_wrap=True)


def _table(*headers: str) -> Table:
    """A table whose cells wrap rather than being cut short."""
    table = Table()
    for header in headers:
        table.add_column(header, overflow="fold")
    return table


HomeOption = Annotated[
    Path | None,
    typer.Option("--home", envvar="PRAT_HOME", help="PRAT home directory (default ~/.prat)."),
]


def _fail(message: str, code: int = 1) -> typer.Exit:
    err.print(f"[red]error:[/red] {escape(message)}")
    return typer.Exit(code)


@app.callback()
def _root(ctx: typer.Context, home: HomeOption = None) -> None:
    ctx.obj = {"home": home}


def _settings(ctx: typer.Context) -> Settings:
    root = ctx.find_root()
    home = root.obj.get("home") if isinstance(root.obj, dict) else None
    try:
        return load_settings(home)
    except ValueError as exc:
        raise _fail(f"invalid settings: {exc}") from exc


NOT_INITIALISED = "PRAT is not initialised here or the database is out of date; run `prat init`"


@contextmanager
def _database(
    ctx: typer.Context, *, require_current: bool = True
) -> Iterator[tuple[Settings, Database]]:
    settings = _settings(ctx)
    if require_current and not settings.database_url and not settings.home.is_dir():
        raise _fail(NOT_INITIALISED)
    from prat.dolt.server import DoltError
    from prat.dolt.store import open_store

    try:
        store = open_store(settings)
    except DoltError as exc:
        raise _fail(str(exc)) from exc
    db = store.db
    try:
        if require_current:
            try:
                current = is_current(db)
            except SQLAlchemyError as exc:
                raise _fail(f"{NOT_INITIALISED} ({exc.__class__.__name__})") from exc
            if not current:
                raise _fail(NOT_INITIALISED)
            # A person at a terminal is using PRAT: the rhythm counts it (prat.rhythm).
            with contextlib.suppress(Exception):
                rhythm.record_signal(db)
        yield settings, db
    finally:
        # Disposes the engine and stops a Dolt server this command started.
        store.close()


@app.command()
def version() -> None:
    """Print the version."""
    console.print(__version__)


@app.command()
def init(ctx: typer.Context) -> None:
    """Create the PRAT home, a default config file and the database."""
    settings = _settings(ctx)
    settings.ensure_dirs()
    if not settings.config_file.exists():
        settings.config_file.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    with _database(ctx, require_current=False) as (_, db):
        upgrade(db)
    from prat.web.tokens import ensure_access_token

    ensure_access_token(settings)
    console.print(f"PRAT home ready at {settings.home}")
    console.print(f"web UI access token: {settings.access_token_file} (keep it private)")


@db_app.command("upgrade")
def db_upgrade(ctx: typer.Context) -> None:
    """Apply database migrations."""
    settings = _settings(ctx)
    settings.ensure_dirs()
    with _database(ctx, require_current=False) as (_, db):
        upgrade(db)
    console.print("database is up to date")


@store_app.command("status")
def store_status(ctx: typer.Context) -> None:
    """Show which store this installation uses and, on Dolt, its latest commits."""
    from prat.dolt import versioning
    from prat.dolt.store import StoreKind, choose, store_dir

    settings = _settings(ctx)
    kind = choose(settings)
    with _database(ctx, require_current=False) as (_, db):
        if kind is StoreKind.DOLT:
            console.print(
                f"store: dolt (PRAT's own server, data in {escape(str(store_dir(settings)))})"
            )
        elif kind is StoreKind.EXTERNAL:
            console.print(f"store: external ({escape(_safe_url(db.url))})")
        else:
            console.print(f"store: sqlite ({escape(str(settings.home / 'prat.db'))})")
        if versioning.is_dolt(db):
            commits = versioning.history(db, limit=5)
            console.print(f"history: {len(commits)} most recent commits")
            for item in commits:
                console.print(
                    f"  {item.hash[:10]} {item.date:%Y-%m-%d %H:%M} {escape(item.message)}"
                )
        else:
            console.print("history, time travel and rollback need the Dolt store")


def _safe_url(url: str) -> str:
    from sqlalchemy.engine import make_url

    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "configured (not shown)"


# Checkpoints ---------------------------------------------------------------------------


@checkpoint_app.command("list")
def checkpoint_list(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option(help="Only this project.")] = None,
) -> None:
    """Every landed card, newest first, with the code and data it landed."""
    from prat.workspace.checkpoints import list_checkpoints

    with _database(ctx) as (_, db):
        rows = list_checkpoints(db, project=project)
    table = _table("card", "project", "landed", "code and data", "state", "title")
    for row in rows:
        table.add_row(
            str(row.card_id),
            escape(row.project_slug),
            f"{row.landed_at:%Y-%m-%d %H:%M}" if row.landed_at else "",
            escape(row.describe()),
            "undone" if row.undone else "landed",
            escape(row.title),
        )
    console.print(table)


@checkpoint_app.command("undo")
def checkpoint_undo(ctx: typer.Context, card_id: int) -> None:
    """Reverse one card's code and data and keep everything that landed since."""
    from prat.workspace.checkpoints import CheckpointError, undo_card

    with _database(ctx) as (settings, db):
        try:
            result = undo_card(
                db, card_id, actor="cli", environ=_checkpoint_environ(db, settings, card_id)
            )
        except CheckpointError as exc:
            raise _fail(str(exc)) from exc
    console.print(f"card {card_id} undone: code reverted as {result.git_revert[:10]}")
    for item in result.databases:
        console.print(f"  data {escape(item['database'])} reverted as {item['revert'][:10]}")


@checkpoint_app.command("rewind")
def checkpoint_rewind(
    ctx: typer.Context,
    card_id: int,
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm: drop everything that landed after this card.")
    ] = False,
) -> None:
    """Put code and data back exactly as they were right after this card landed."""
    from prat.workspace.checkpoints import CheckpointError, rewind_to

    with _database(ctx) as (settings, db):
        try:
            result = rewind_to(
                db,
                card_id,
                confirm=yes,
                actor="cli",
                environ=_checkpoint_environ(db, settings, card_id),
            )
        except CheckpointError as exc:
            raise _fail(str(exc)) from exc
    console.print(f"rewound to card {card_id}: code at {result.git_head[:10]}")
    for item in result.databases:
        console.print(f"  data {escape(item['database'])} at {item['head'][:10]}")
    console.print(f"the previous state is tagged {result.backup_tag} in the code and the data")


# Projects ----------------------------------------------------------------------


def _checkpoint_environ(db: Database, settings: Settings, card_id: int) -> dict[str, str]:
    from prat.environments import database_environ

    with db.session() as session:
        slug = store.get_card(session, card_id).project.slug
    return database_environ(settings.home, slug)


@project_app.command("add")
def project_add(
    ctx: typer.Context,
    repository: Annotated[
        str,
        typer.Argument(help="A repository link (https or ssh) to clone, or your own clone's path."),
    ],
    branch: Annotated[
        str | None,
        typer.Option(help="Branch to work from, for a link (default: the repository's default)."),
    ] = None,
    slug: Annotated[str | None, typer.Option(help="Short identifier (default: from name).")] = None,
    agent_path: Annotated[
        Path | None,
        typer.Option(help="Where PRAT keeps its own clone (default: <checkout>-agent)."),
    ] = None,
    setup: Annotated[
        bool,
        typer.Option(
            "--setup/--no-setup",
            help="Without a project file, add a setup card whose agent writes one.",
        ),
    ] = True,
) -> None:
    """Register a repository: clone it from a link (into projects_dir), or use your own clone.
    Reads .prat/project.toml if present; without one, a setup card is added to write it."""
    from prat import onboard
    from prat.setup_agent import add_setup_card, needs_setup

    with _database(ctx) as (settings, db):
        if onboard.is_link(repository):
            try:
                found = onboard.list_branches(repository)
                chosen = branch or found.default or found.names[0]
                if chosen not in found.names:
                    raise onboard.OnboardError(
                        f"no branch {chosen!r}; branches: {', '.join(found.names[:20])}"
                    )
                name = slug or onboard.slug_for(repository)
                destination = onboard.projects_dir(settings) / name
                console.print(f"cloning {chosen} of {escape(repository)} into {destination}")
                checkout = onboard.clone(repository, chosen, destination)
            except onboard.OnboardError as exc:
                raise _fail(str(exc)) from exc
            slug = name
        else:
            checkout = Path(repository).expanduser().resolve()
            chosen = None
        if not (checkout / ".git").exists():
            raise _fail(f"{checkout} is not a git checkout")
        try:
            spec = load_spec(checkout)
        except ProjectFileError as exc:
            raise _fail(str(exc)) from exc
        if chosen is not None and spec.default_branch != chosen:
            spec = spec.model_copy(update={"default_branch": chosen})
        with db.session() as session:
            try:
                project = store.add_project(
                    session,
                    slug=slug or slugify(spec.name, 63),
                    checkout_path=checkout,
                    agent_path=agent_path or default_agent_path(checkout),
                    spec=spec,
                )
            except (ValueError, PermissionError) as exc:
                raise _fail(str(exc)) from exc
            console.print(f"added project [bold]{project.slug}[/bold] (id {project.id})")
            console.print(f"  checkout:   {project.checkout_path}")
            console.print(f"  branch:     {project.default_branch}")
            console.print(f"  agent clone: {project.agent_path} (created on first run)")
            if needs_setup(checkout) and setup:
                card = add_setup_card(session, project, settings)
                console.print(
                    f"\nNo {PROJECT_FILE_REL} yet: setup card #{card.id} is ready. Its agent "
                    "works out how to install, test and run the project; run it from the board "
                    f"or with [bold]prat card run {card.id}[/bold], then review and approve it."
                )
            elif needs_setup(checkout):
                console.print(
                    f"\nNo {PROJECT_FILE_REL} yet, so no gates run before review. Run "
                    f"[bold]prat project suggest {checkout}[/bold] for a starter file to commit."
                )


@project_app.command("remove")
def project_remove(
    ctx: typer.Context,
    slug: str,
    delete_clone: Annotated[
        bool, typer.Option("--delete-clone", help="Also delete PRAT's own clone.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")] = False,
) -> None:
    """Forget a project and its cards. Your checkout is never touched; the project's knowledge
    and environment file are kept under $PRAT_HOME/removed."""
    import shutil
    from datetime import UTC, datetime

    from prat.environments import overrides_path
    from prat.git.worktree import rmtree
    from prat.knowledge.entries import KnowledgeBase

    if not yes and not typer.confirm(f"Remove project {slug} and all its cards from PRAT?"):
        raise typer.Exit(1)
    with _database(ctx) as (settings, db), db.session() as session:
        try:
            slug, checkout, agent = store.remove_project(session, slug)
        except (store.NotFound, ValueError) as exc:
            raise _fail(str(exc)) from exc
    archive = settings.home / "removed" / f"{slug}-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    for source in (KnowledgeBase(settings.home, slug).root, overrides_path(settings.home, slug)):
        if source.exists():
            archive.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(archive / source.name))
    console.print(
        f"removed project {escape(slug)}; your checkout {escape(str(checkout))} is untouched"
    )
    if archive.exists():
        console.print(f"knowledge and environments kept in {escape(str(archive))}")
    if delete_clone and agent.exists() and agent.resolve() != checkout.resolve():
        rmtree(agent)
        console.print(f"deleted PRAT's clone {escape(str(agent))}")


@project_app.command("suggest")
def project_suggest(
    checkout: Annotated[Path, typer.Argument(help="Path to your own clone of the repository.")],
) -> None:
    """Print a starter .prat/project.toml for a checkout. Reads only; writes nothing."""
    checkout = checkout.expanduser().resolve()
    if not checkout.is_dir():
        raise _fail(f"{checkout} is not a directory")
    found = project_detect.suggest(checkout)
    err.print(
        f"Detected: {', '.join(found.stacks) or 'nothing familiar'}. "
        f"Save this as {PROJECT_FILE_REL}, review it, and commit it to {found.default_branch}."
    )
    for note in found.notes:
        err.print(f"Note: {note}")
    typer.echo(found.toml(), nl=False)


@project_app.command("list")
def project_list(ctx: typer.Context) -> None:
    """List registered projects."""
    with _database(ctx) as (_, db), db.session() as session:
        table = _table("id", "slug", "name", "default branch", "checkout")
        for project in store.list_projects(session):
            table.add_row(
                str(project.id),
                project.slug,
                project.name,
                project.default_branch,
                project.checkout_path,
            )
        console.print(table)


@project_app.command("setup")
def project_setup(ctx: typer.Context, slug: str) -> None:
    """Add a setup card for a registered project: its agent works out how to install, test and
    run it and proposes .prat/project.toml. Run it from the board or with prat card run."""
    from prat.setup_agent import add_setup_card

    with _database(ctx) as (settings, db), db.session() as session:
        try:
            project = store.get_project(session, slug)
        except store.NotFound as exc:
            raise _fail(str(exc)) from exc
        card = add_setup_card(session, project, settings)
        console.print(
            f"setup card #{card.id} is ready for {escape(project.slug)}; run it from the board "
            f"or with [bold]prat card run {card.id}[/bold], then review and approve it"
        )


@project_app.command("set")
def project_set(
    ctx: typer.Context,
    slug: str,
    autonomy: Annotated[
        Autonomy | None, typer.Option(help="read_only, low, medium or high.")
    ] = None,
) -> None:
    """Change a project's settings kept in PRAT, for a repository without .prat/project.toml.
    A committed project file always wins; the installation's ceiling still applies."""
    if autonomy is None:
        raise _fail("nothing to set; pass --autonomy")
    with _database(ctx) as (settings, db), db.session() as session:
        try:
            project = store.get_project(session, slug)
        except store.NotFound as exc:
            raise _fail(str(exc)) from exc
        config = dict(project.config or {})
        config["autonomy"] = str(autonomy)
        project.config = config
        store.record_event(
            session, "project.settings", project_id=project.id, data={"autonomy": str(autonomy)}
        )
        console.print(f"{escape(project.slug)}: autonomy {autonomy}")
        if Autonomy(autonomy).at_least(settings.max_autonomy) and autonomy != settings.max_autonomy:
            console.print(
                f"[yellow]note:[/yellow] the installation ceiling is {settings.max_autonomy}; "
                "runs use the lower of the two"
            )


@project_app.command("show")
def project_show(
    ctx: typer.Context, ref: Annotated[str, typer.Argument(help="Slug or id.")]
) -> None:
    """Show a project's paths and configuration."""
    with _database(ctx) as (_, db), db.session() as session:
        try:
            project = store.get_project(session, ref)
        except store.NotFound as exc:
            raise _fail(str(exc)) from exc
        console.print(f"[bold]{project.slug}[/bold] (id {project.id}): {project.name}")
        console.print(f"checkout:       {project.checkout_path}")
        console.print(f"agent clone:    {project.agent_path}")
        console.print(f"default branch: {project.default_branch}")
        console.print_json(data=project.config)


# Cards ---------------------------------------------------------------------------


@card_app.command("add")
def card_add(
    ctx: typer.Context,
    project: Annotated[str, typer.Argument(help="Project slug or id.")],
    title: Annotated[str, typer.Argument(help="Short title.")],
    goal: Annotated[str, typer.Option(help="What should be achieved.")] = "",
    criteria: Annotated[str, typer.Option("--criteria", help="Acceptance criteria.")] = "",
    complexity: Annotated[
        Complexity, typer.Option(help="low, medium or high.")
    ] = Complexity.MEDIUM,
    autonomy: Annotated[
        Autonomy | None, typer.Option(help="Lower the project's autonomy for this card.")
    ] = None,
    budget: Annotated[float | None, typer.Option(help="Budget in USD for each run.")] = None,
    ready: Annotated[bool, typer.Option("--ready", help="Create the card as ready.")] = False,
) -> None:
    """Create a card (draft unless --ready)."""
    with _database(ctx) as (_, db), db.session() as session:
        try:
            proj = store.get_project(session, project)
            card = store.add_card(
                session,
                proj,
                title=title,
                goal=goal,
                acceptance_criteria=criteria,
                complexity=complexity,
                ready=ready,
                autonomy=autonomy,
                budget_usd=budget,
            )
        except (store.NotFound, ValueError) as exc:
            raise _fail(str(exc)) from exc
        console.print(f"card {card.id} created ({card.status})")


@card_app.command("list")
def card_list(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option(help="Only this project.")] = None,
) -> None:
    """List cards."""
    with _database(ctx) as (_, db), db.session() as session:
        proj = store.get_project(session, project) if project else None
        table = _table("id", "project", "status", "title", "branch")
        for card in store.list_cards(session, proj):
            table.add_row(
                str(card.id), card.project.slug, str(card.status), card.title, card.branch or ""
            )
        console.print(table)


@card_app.command("show")
def card_show(
    ctx: typer.Context,
    card_id: int,
    journal: Annotated[bool, typer.Option(help="Print the latest run's journal.")] = False,
) -> None:
    """Show a card, its runs, comments and history."""
    with _database(ctx) as (_, db), db.session() as session:
        try:
            card = store.get_card(session, card_id)
        except store.NotFound as exc:
            raise _fail(str(exc)) from exc
        # Card text, agent output and journal data are content, never console markup.
        console.print(f"[bold]Card {card.id}[/bold]: {escape(card.title)}")
        console.print(
            f"project: {card.project.slug}   status: {card.status}"
            + (f"   reason: {escape(card.status_reason)}" if card.status_reason else "")
        )
        if card.branch:
            console.print(f"branch:  {escape(card.branch)}")
        if card.goal:
            console.print(f"\n[bold]Goal[/bold]\n{escape(card.goal)}")
        if card.acceptance_criteria:
            console.print(f"\n[bold]Acceptance criteria[/bold]\n{escape(card.acceptance_criteria)}")
        if card.summary:
            console.print(f"\n[bold]Summary (from the diff)[/bold]\n{escape(card.summary)}")
        if card.runs:
            table = _table("run", "status", "reason", "steps")
            for run in card.runs:
                steps = " ".join(f"{s.name}:{s.status}" for s in run.steps)
                table.add_row(str(run.id), str(run.status), escape(run.reason or ""), steps)
            console.print(table)
        for comment in card.comments:
            console.print(
                f"\n[bold]{comment.author}[/bold] ({comment.kind}, "
                f"{comment.created_at:%Y-%m-%d %H:%M})\n{escape(comment.body)}"
            )
        if journal and card.runs and card.runs[-1].journal_path:
            console.print("\n[bold]Journal[/bold]")
            for entry in read_journal(Path(card.runs[-1].journal_path)):
                console.print(
                    f"{entry.seq:>4} {entry.ts:%H:%M:%S} {entry.kind} {escape(str(entry.data))}"
                )


RUNTIMES = ("claude", "fake")


def _make_runtime(settings: Settings, runtime: str | None, script: Path | None) -> AgentRuntime:
    name = runtime or ("fake" if script is not None else "claude")
    if name not in RUNTIMES:
        raise _fail(f"unknown runtime {name!r}; use one of {', '.join(RUNTIMES)}")
    if name == "claude":
        if script is not None:
            raise _fail("--script only applies to the fake runtime")
        from prat.runtime.auth import AuthError
        from prat.runtime.claude import ClaudeRuntime

        try:
            return ClaudeRuntime.from_settings(settings)
        except AuthError as exc:
            raise _fail(f"{exc} (see `prat auth status`)") from exc
    if script is None:
        raise _fail("--script is required with the fake runtime")
    try:
        parsed = FakeScript.load(script)
    except (OSError, ValueError) as exc:
        raise _fail(f"could not load script {script}: {exc}") from exc
    return FakeRuntime(parsed, state_dir=settings.home / "fake-sessions")


def _make_engine(settings: Settings, db: Database, agent: AgentRuntime) -> Engine:
    # Every agent reads the installation overlay's guidance for its role.
    agent = OverlayGuidedRuntime(agent, settings.home)
    reviewer: Reviewer
    planner: Planner
    security: SecurityReviewer
    if agent.name == "fake":
        reviewer, planner, security = FakeReviewer(), NoPlanner(), FakeSecurityReviewer()
    else:
        from prat.engine.critic import AgentReviewer
        from prat.engine.planner import AgentPlanner
        from prat.engine.security import AgentSecurityReviewer

        reviewer, planner = AgentReviewer(agent), AgentPlanner(agent)
        security = AgentSecurityReviewer(agent)
    return Engine(
        settings, db, agent, reviewer=reviewer, planner=planner, security_reviewer=security
    )


def _print_result(result: RunResult) -> None:
    colour = {CardStatus.REVIEW: "green", CardStatus.BLOCKED: "yellow"}.get(
        result.card_status, "red"
    )
    console.print(
        f"card {result.card_id}: run {result.run_id} {result.run_status} -> "
        f"[{colour}]{result.card_status}[/{colour}]"
        + (
            f" ({escape(result.reason)})"
            if result.reason and result.card_status != CardStatus.REVIEW
            else ""
        )
    )


RuntimeOption = Annotated[
    str | None,
    typer.Option(help="Agent runtime: claude (default) or fake (default when --script is given)."),
]
ScriptOption = Annotated[Path | None, typer.Option(help="JSON script for the fake runtime.")]


@card_app.command("run")
def card_run(
    ctx: typer.Context,
    card_id: int,
    runtime: RuntimeOption = None,
    script: ScriptOption = None,
) -> None:
    """Run a card now (moves a draft card to ready first)."""
    with _database(ctx) as (settings, db):
        agent = _make_runtime(settings, runtime, script)
        with db.session() as session:
            try:
                card = store.get_card(session, card_id)
            except store.NotFound as exc:
                raise _fail(str(exc)) from exc
            if card.status in (CardStatus.DRAFT, CardStatus.BLOCKED, CardStatus.FAILED):
                store.set_card_status(session, card, CardStatus.READY)
        engine = _make_engine(settings, db, agent)
        try:
            result = asyncio.run(engine.run_card(card_id))
        except (EngineError, CloneError, GitError) as exc:
            raise _fail(str(exc)) from exc
        _print_result(result)
        if result.card_status != CardStatus.REVIEW:
            raise typer.Exit(2)


@card_app.command("resume")
def card_resume(
    ctx: typer.Context,
    card_id: int,
    runtime: RuntimeOption = None,
    script: ScriptOption = None,
) -> None:
    """Resume a card's run that was interrupted by a crash."""
    with _database(ctx) as (settings, db):
        agent = _make_runtime(settings, runtime, script)
        engine = _make_engine(settings, db, agent)
        engine.recover()
        with db.session() as session:
            card = store.get_card(session, card_id)
            run = store.latest_run(session, card)
            if run is None:
                raise _fail(f"card {card_id} has no runs")
            run_id = run.id
        try:
            result = asyncio.run(engine.resume(run_id))
        except EngineError as exc:
            raise _fail(str(exc)) from exc
        _print_result(result)


@card_app.command("approve")
def card_approve(ctx: typer.Context, card_id: int) -> None:
    """Merge a reviewed card into the agent clone's default branch (local, no push)."""
    with _database(ctx) as (settings, db):
        engine = Engine(settings, db, FakeRuntime([]))
        try:
            merged = asyncio.run(engine.approve(card_id))
        except (ApproveError, store.NotFound, GitError) as exc:
            raise _fail(str(exc)) from exc
        with db.session() as session:
            project = store.get_card(session, card_id).project
            agent_path, default = project.agent_path, project.default_branch
        console.print(f"merged {merged.branch} into {default} as {merged.commit[:10]}")
        console.print(f"to bring it into your checkout: git pull {agent_path} {default}")


@card_app.command("cancel")
def card_cancel(ctx: typer.Context, card_id: int) -> None:
    """Cancel a card. A run in another PRAT process is asked to stop."""
    with _database(ctx) as (settings, db):
        engine = Engine(settings, db, FakeRuntime([]))
        try:
            changed = asyncio.run(engine.cancel(card_id))
        except store.NotFound as exc:
            raise _fail(str(exc)) from exc
        console.print("cancelled" if changed else "nothing to cancel")


@card_app.command("comment")
def card_comment(
    ctx: typer.Context,
    card_id: int,
    text: Annotated[str, typer.Argument(help="The comment.")],
) -> None:
    """Add a human comment to a card (used as feedback by the next rework run)."""
    if not text.strip():
        raise _fail("the comment is empty")
    with _database(ctx) as (_, db), db.session() as session:
        try:
            card = store.get_card(session, card_id)
        except store.NotFound as exc:
            raise _fail(str(exc)) from exc
        store.add_comment(session, card, text, author=CommentAuthor.HUMAN, kind="comment")
        console.print(f"comment added to card {card_id}")


@card_app.command("rework")
def card_rework(
    ctx: typer.Context,
    card_id: int,
    feedback: Annotated[
        str | None,
        typer.Option(help="What to change. Optional if you already added a comment."),
    ] = None,
) -> None:
    """Send a card back for rework. Run it again with `prat card run`.

    The next run gets the human comments added since the last run and the
    previous reviewer verdict, and continues on the same branch.
    """
    with _database(ctx) as (settings, db):
        engine = Engine(settings, db, FakeRuntime([]))
        try:
            engine.rework(card_id, feedback)
        except (EngineError, store.NotFound, ValueError) as exc:
            raise _fail(str(exc)) from exc
        console.print(f"card {card_id} set to rework")


# Auth --------------------------------------------------------------------------------


@auth_app.command("status")
def auth_status(ctx: typer.Context) -> None:
    """Show which authentication mode is active and whether it is usable."""
    from prat.runtime.auth import check_auth

    settings = _settings(ctx)
    status = check_auth(settings)
    colour = "green" if status.usable else "red"
    console.print(f"mode:   {status.mode}")
    console.print(f"usable: [{colour}]{'yes' if status.usable else 'no'}[/{colour}]")
    console.print(f"detail: {escape(status.detail)}")
    for role in Role:
        console.print(f"model ({role}): {resolve_model(role, settings_models=settings.models)}")
    if not status.usable:
        raise typer.Exit(1)


# Self-improvement ----------------------------------------------------------------------


def _improve_settings(db: Database, **changes: object) -> None:
    from prat.improve.settings import update_improve_settings

    with db.session() as session:
        try:
            update_improve_settings(session, actor="cli", **changes)
        except ValueError as exc:
            raise _fail(str(exc)) from exc


def _environment_manager(settings: Settings) -> EnvironmentManager:
    from prat.environments import EnvironmentManager
    from prat.sandbox import environment_sandboxes

    return EnvironmentManager(settings.home, environment_sandboxes(settings))


@sandbox_app.command("check")
def sandbox_check(ctx: typer.Context) -> None:
    """Whether the sandbox can run here: docker, and the image."""
    from prat import sandbox

    settings = _settings(ctx)
    problem = sandbox.check(settings.docker_path, settings.sandbox_image)
    state = "on" if settings.sandbox == "docker" else 'off (sandbox = "docker" turns it on)'
    console.print(f"sandbox: {state}")
    if problem is not None:
        raise _fail(problem)
    console.print(f"image {settings.sandbox_image} is ready")


@sandbox_app.command("build")
def sandbox_build(ctx: typer.Context) -> None:
    """Build PRAT's sandbox image (Python, Node, build tools, database clients)."""
    import subprocess

    from prat import sandbox

    settings = _settings(ctx)
    if not sandbox.supported():
        raise _fail("the sandbox runs on macOS and Linux only")
    dockerfile = sandbox.dockerfile()
    command = [
        settings.docker_path, "build", "-t", settings.sandbox_image,
        "-f", str(dockerfile), str(dockerfile.parent),
    ]  # fmt: skip
    if subprocess.run(command, check=False).returncode != 0:
        raise _fail("docker build failed")
    console.print(f"built {settings.sandbox_image}")


def _environments(
    ctx: typer.Context, slug: str
) -> tuple[Settings, Path, list[Environment], dict[str, str]]:
    from prat.environments import checkout_environment_variables, project_environments

    with _database(ctx) as (settings, db):
        try:
            checkout, envs, problem = project_environments(db, settings.home, slug)
            variables = checkout_environment_variables(db, settings.home, slug)
        except LookupError as exc:
            raise _fail(str(exc)) from exc
    if problem:
        err.print(f"[yellow]warning:[/yellow] {escape(problem)}")
    return settings, checkout, envs, variables


def _environment(envs: list[Environment], name: str) -> Environment:
    for env in envs:
        if env.name == name:
            return env
    raise _fail(f"no environment {name!r}; known: {', '.join(e.name for e in envs) or 'none'}")


@env_app.command("list")
def env_list(ctx: typer.Context, slug: str) -> None:
    """A project's environments, whether each is running, and its links."""

    settings, _, envs, _variables = _environments(ctx, slug)
    manager = _environment_manager(settings)
    table = _table("name", "label", "state", "port", "links")
    for env in envs:
        view = manager.view(slug, env)
        links = " ".join(f"{label}: {url}" for label, url in view.links)
        table.add_row(env.name, escape(env.title), view.state, str(view.port or ""), links)
    console.print(table)


@env_app.command("start")
def env_start(ctx: typer.Context, slug: str, name: str) -> None:
    """Start an environment in your checkout. It keeps running after this command ends."""
    from prat.environments import EnvironmentError

    settings, checkout, envs, variables = _environments(ctx, slug)
    env = _environment(envs, name)
    try:
        view = _environment_manager(settings).start(slug, env, checkout, extra_env=variables)
    except EnvironmentError as exc:
        raise _fail(str(exc)) from exc
    console.print(f"{escape(env.title)} started (process {view.pid}, port {view.port or 'none'})")
    for label, url in view.links:
        console.print(f"  {escape(label)}: {url}")


@env_app.command("stop")
def env_stop(ctx: typer.Context, slug: str, name: str) -> None:
    """Stop an environment and everything it started."""

    settings, _, envs, _variables = _environments(ctx, slug)
    env = _environment(envs, name)
    _environment_manager(settings).stop(slug, env)
    console.print(f"{escape(env.title)} stopped")


@env_app.command("log")
def env_log(ctx: typer.Context, slug: str, name: str) -> None:
    """The end of an environment's output."""

    settings, _, envs, _variables = _environments(ctx, slug)
    env = _environment(envs, name)
    typer.echo(_environment_manager(settings).view(slug, env).log_tail, nl=False)


@overlay_app.command("init")
def overlay_init(ctx: typer.Context) -> None:
    """Create the overlay folder in the PRAT home, as its own git repository."""
    from prat.overlay import init_overlay

    settings = _settings(ctx)
    path = init_overlay(
        settings.home,
        author_name=settings.git_author_name,
        author_email=settings.git_author_email,
    )
    console.print(f"overlay ready at {escape(str(path))}")


@overlay_app.command("show")
def overlay_show(ctx: typer.Context) -> None:
    """What the overlay adds, and anything switched off."""
    from prat.overlay import load_overlay, overlay_path

    settings = _settings(ctx)
    found = load_overlay(overlay_path(settings.home))
    if not found.present:
        console.print("no overlay (create one with `prat overlay init`)")
        return
    console.print(f"overlay: {escape(found.name or str(found.path))} (overlay_api {found.api})")
    for item in found.loaded:
        console.print(f"  applied: {escape(item)}")
    for issue in found.disabled:
        reason = escape(issue.reason)
        console.print(f"  [yellow]switched off[/yellow]: {escape(issue.item)}: {reason}")
    for rule in found.denied_commands:
        console.print(f"  denies command {escape(rule.pattern)}: {escape(rule.reason)}")
    for path_rule in found.denied_paths:
        kind = "reading or writing" if path_rule.read else "writing"
        console.print(f"  denies {kind} {escape(path_rule.pattern)}: {escape(path_rule.reason)}")
    if found.require_person:
        console.print(f"  needs a person for: {', '.join(found.require_person)}")
    for role in found.guidance:
        console.print(f"  guidance for {role}")
    for role, model in found.models.items():
        console.print(f"  model for {role}: {escape(model)}")


@overlay_app.command("validate")
def overlay_validate(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Option(help="An overlay folder to check instead of this installation's."),
    ] = None,
) -> None:
    """Exit with status 1 if any part of the overlay is switched off."""
    from prat.overlay import load_overlay, overlay_path

    folder = path.resolve() if path is not None else overlay_path(_settings(ctx).home)
    found = load_overlay(folder)
    if not found.present:
        err.print(f"{folder} is not an overlay folder")
        raise typer.Exit(1)
    for issue in found.disabled:
        err.print(f"{issue.item}: {issue.reason}")
    if found.disabled:
        raise typer.Exit(1)
    console.print(f"overlay valid: {len(found.loaded)} file(s) applied")


@overlay_app.command("log")
def overlay_log_command(ctx: typer.Context) -> None:
    """Recent changes to the overlay, newest first."""
    from prat.overlay import overlay_log

    table = _table("commit", "date", "change")
    for commit in overlay_log(_settings(ctx).home):
        table.add_row(commit.sha[:10], commit.date, escape(commit.subject))
    console.print(table)


@overlay_app.command("undo")
def overlay_undo(ctx: typer.Context, commit: str) -> None:
    """Reverse one change to the overlay with a new commit."""
    from prat.overlay import OverlayError, undo_overlay_commit

    try:
        new = undo_overlay_commit(_settings(ctx).home, commit)
    except OverlayError as exc:
        raise _fail(str(exc)) from exc
    console.print(f"overlay change {commit[:10]} undone as {new[:10]}")


@improve_app.command("status")
def improve_status(ctx: typer.Context) -> None:
    """Show the toggle, the self project, the last cycle, spend and findings."""
    from sqlalchemy import func, select

    from prat.db.models import ImproveCycle, ImproveFinding
    from prat.improve.cycle import open_self_cards, spent_today
    from prat.improve.scheduler import may_start
    from prat.improve.selfproject import get_self_project, load_self_info
    from prat.improve.settings import load_improve_settings

    with _database(ctx) as (_, db), db.session() as session:
        conf = load_improve_settings(session)
        state = "[green]on[/green]" if conf.enabled else "off"
        console.print(f"self-improvement: {state}, mode {conf.mode}")
        project, info = get_self_project(session), load_self_info(session)
        if project is None:
            console.print("self project: not set up (run `prat improve setup`)")
        else:
            push = "available" if info and info.push_available else "unavailable"
            console.print(f"self project: {escape(project.agent_path)} ({project.default_branch})")
            if info is not None:
                console.print(f"remote: {escape(info.origin)} (push {push})")
        cycle = session.scalars(select(ImproveCycle).order_by(ImproveCycle.id.desc())).first()
        if cycle is not None:
            decision = (cycle.summary or {}).get("decision") or cycle.error or ""
            console.print(
                f"last cycle: #{cycle.id} {cycle.trigger} {cycle.status} at "
                f"{cycle.started_at:%Y-%m-%d %H:%M} UTC {escape(str(decision))}"
            )
        console.print(f"next scheduled cycle: {may_start(db).reason}")
        console.print(
            f"spent today: ${spent_today(session):.2f} of ${conf.daily_budget_usd:.2f}; "
            f"open self cards: {len(open_self_cards(session))} of {conf.max_open_cards}"
        )
        counts = session.execute(
            select(ImproveFinding.status, func.count()).group_by(ImproveFinding.status)
        ).all()
        if counts:
            console.print(
                "findings: " + ", ".join(f"{count} {status}" for status, count in sorted(counts))
            )
        table = _table("setting", "value")
        for name, value in conf.model_dump(mode="json").items():
            table.add_row(name, str(value))
        console.print(table)


@improve_app.command("on")
def improve_on(ctx: typer.Context) -> None:
    """Turn self-improvement on."""
    from prat.improve.selfproject import get_self_project

    with _database(ctx) as (_, db):
        _improve_settings(db, enabled=True)
        with db.session() as session:
            ready = get_self_project(session) is not None
    console.print("self-improvement is on")
    if not ready:
        console.print("it waits until the self project is set up: `prat improve setup`")


@improve_app.command("off")
def improve_off(ctx: typer.Context) -> None:
    """Turn self-improvement off. Cards already running finish and wait in review."""
    with _database(ctx) as (_, db):
        _improve_settings(db, enabled=False)
    console.print("self-improvement is off")


@improve_app.command("mode")
def improve_mode(ctx: typer.Context, mode: str) -> None:
    """auto: proven changes land and push by themselves. review: a person approves each."""
    with _database(ctx) as (_, db):
        _improve_settings(db, mode=mode)
    console.print(f"mode set to {mode}")


@improve_app.command("set")
def improve_set(ctx: typer.Context, name: str, value: str) -> None:
    """Change one setting, for example `prat improve set daily_budget_usd 3`."""
    import json

    try:
        parsed: object = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    with _database(ctx) as (_, db):
        _improve_settings(db, **{name: parsed})
    console.print(f"{name} set to {parsed}")


@improve_app.command("setup")
def improve_setup(
    ctx: typer.Context,
    repo_url: Annotated[
        str | None,
        typer.Option(
            "--repo-url",
            help="PRAT's repository to clone and push to (default: the origin of the "
            "checkout PRAT runs from).",
        ),
    ] = None,
) -> None:
    """Create or refresh the self project: a hardened clone of PRAT's own source."""
    from prat.improve.selfproject import SelfProjectError, setup_self_project

    with _database(ctx) as (settings, db):
        try:
            result = setup_self_project(db, settings, repo_url=repo_url, actor="cli")
        except SelfProjectError as exc:
            raise _fail(str(exc)) from exc
    console.print(
        f"self project {'created' if result.created else 'updated'} at {escape(str(result.path))}"
    )
    console.print(f"source: {escape(result.info.origin)} ({escape(result.info.reason)})")
    console.print(f"default branch {result.default_branch} at {result.head[:10]}")
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {escape(warning)}")


@improve_app.command("run")
def improve_run(
    ctx: typer.Context, runtime: RuntimeOption = None, script: ScriptOption = None
) -> None:
    """Run one cycle now. Cards it files run under `prat serve` or with `prat card run`."""
    from prat.domain import CycleStatus, CycleTrigger
    from prat.improve.cycle import run_cycle

    with _database(ctx) as (settings, db):
        agent = OverlayGuidedRuntime(_make_runtime(settings, runtime, script), settings.home)
        result = asyncio.run(run_cycle(db, settings, agent, trigger=CycleTrigger.MANUAL))
    label = f"cycle #{result.cycle_id}" if result.cycle_id else "cycle"
    console.print(f"{label}: {result.status}: {escape(result.message)}")
    for card_id in result.cards:
        console.print(f"filed card {card_id}")
    if result.status not in (CycleStatus.SUCCEEDED, CycleStatus.SKIPPED):
        raise typer.Exit(2)


@improve_app.command("findings")
def improve_findings(
    ctx: typer.Context,
    status: Annotated[str | None, typer.Option(help="new, carded, landed, ...")] = None,
    source: Annotated[str | None, typer.Option(help="diagnostic, research or roadmap")] = None,
) -> None:
    """List findings, newest first."""
    from prat.domain import FindingSource, FindingStatus
    from prat.improve.findings import list_findings

    try:
        status_value = FindingStatus(status) if status else None
        source_value = FindingSource(source) if source else None
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    with _database(ctx) as (_, db), db.session() as session:
        rows = list_findings(session, status=status_value, source=source_value)
        table = _table("id", "source", "status", "severity", "effort", "seen", "card", "title")
        for row in rows:
            table.add_row(
                str(row.id),
                str(row.source),
                str(row.status),
                str(row.severity),
                str(row.effort),
                str(row.seen_count),
                str(row.card_id or ""),
                escape(row.title),
            )
    console.print(table)


@improve_app.command("dismiss")
def improve_dismiss(
    ctx: typer.Context,
    finding_id: int,
    note: Annotated[str, typer.Option(help="Why it is dismissed.")] = "",
) -> None:
    """Dismiss a finding so it is never filed as a card."""
    from prat.improve.findings import dismiss_finding

    with _database(ctx) as (_, db), db.session() as session:
        try:
            dismiss_finding(session, finding_id, note=note, actor="cli")
        except (LookupError, ValueError) as exc:
            raise _fail(str(exc)) from exc
    console.print(f"finding {finding_id} dismissed")


@improve_app.command("allow")
def improve_allow(ctx: typer.Context, finding_id: int) -> None:
    """Let a finding the research screen held back into the queue."""
    from prat.improve.findings import allow_screened

    with _database(ctx) as (_, db), db.session() as session:
        try:
            allow_screened(session, finding_id, actor="cli")
        except (LookupError, ValueError) as exc:
            raise _fail(str(exc)) from exc
    console.print(f"finding {finding_id} is back in the queue")


# Web UI --------------------------------------------------------------------------------


def _serve_runtime(settings: Settings, runtime: str | None, script: Path | None) -> AgentRuntime:
    name = runtime or ("fake" if script is not None else "claude")
    if name != "claude":
        return _make_runtime(settings, name, script)
    if script is not None:
        raise _fail("--script only applies to the fake runtime")
    from prat.runtime.auth import check_auth
    from prat.runtime.claude import ClaudeRuntime
    from prat.web.server import DeferredRuntime

    status = check_auth(settings)
    if not status.usable:
        err.print(
            f"[yellow]warning:[/yellow] agent authentication is not usable yet: "
            f"{escape(status.detail)}. Runs will fail until it is fixed (see Settings)."
        )
    return DeferredRuntime("claude", lambda: ClaudeRuntime.from_settings(settings))


@app.command()
def serve(
    ctx: typer.Context,
    host: Annotated[
        str | None, typer.Option(help="Address to listen on (default 127.0.0.1).")
    ] = None,
    port: Annotated[int | None, typer.Option(help="Port (default 18300).")] = None,
    runtime: RuntimeOption = None,
    script: ScriptOption = None,
    allow_remote: Annotated[
        bool,
        typer.Option("--allow-remote", help="Allow a non-loopback address (put TLS in front)."),
    ] = False,
) -> None:
    """Start the web UI and the run engine. Prints a one-time login link."""
    from prat.web import BindError, check_bind, create_app
    from prat.web.server import run_server
    from prat.web.tokens import ensure_access_token, login_url, origin_for

    with _database(ctx) as (settings, db):
        bind_host = host or settings.web_host
        bind_port = port or settings.web_port
        try:
            warning = check_bind(
                bind_host,
                allow_remote=allow_remote or settings.web_allow_remote,
                public_url=settings.web_public_url,
            )
            agent = _serve_runtime(settings, runtime, script)
            engine = _make_engine(settings, db, agent)
            web = create_app(
                settings, db, engine, runtime_name=agent.name, host=bind_host, port=bind_port
            )
        except BindError as exc:
            raise _fail(str(exc), 2) from exc
        if warning:
            err.print(f"[yellow]{escape(warning)}[/yellow]")
        ensure_access_token(settings)
        typer.echo(
            f"PRAT web UI: {origin_for(settings, bind_host, bind_port)}  (runtime: {agent.name})"
        )
        typer.echo("Sign in with this one-time link (valid for 10 minutes):")
        typer.echo("  " + login_url(settings, host=bind_host, port=bind_port))
        typer.echo("Lost it? Run `prat login-url` for a new one. Press Ctrl+C to stop.")
        run_server(settings, web, host=bind_host, port=bind_port)


@app.command("login-url")
def login_url_command(
    ctx: typer.Context,
    next_path: Annotated[str, typer.Option("--next", help="Page to open after signing in.")] = "/",
) -> None:
    """Print a new one-time login link for the web UI."""
    from prat.web.tokens import login_url

    settings = _settings(ctx)
    if not settings.home.is_dir():
        raise _fail(NOT_INITIALISED)
    if not next_path.startswith("/") or next_path.startswith("//"):
        raise _fail("--next must be a path on the PRAT server, such as /board")
    typer.echo(login_url(settings, next_path=next_path))


@app.command("rotate-token")
def rotate_token(ctx: typer.Context) -> None:
    """Replace the web UI access token. Signs out every session and voids login links."""
    from prat.web.tokens import rotate_access_token

    settings = _settings(ctx)
    if not settings.home.is_dir():
        raise _fail(NOT_INITIALISED)
    rotate_access_token(settings)
    console.print(f"new access token written to {settings.access_token_file}")


def _knowledge_base(ctx: typer.Context, slug: str) -> tuple[Settings, KnowledgeBase, Path, str]:
    from prat.knowledge.entries import KnowledgeBase

    with _database(ctx) as (settings, db), db.session() as session:
        try:
            project = store.get_project(session, slug)
        except LookupError as exc:
            raise _fail(str(exc)) from exc
        slug, agent, branch = project.slug, Path(project.agent_path), project.default_branch
    return settings, KnowledgeBase(settings.home, slug), agent, branch


def _entry(kb: KnowledgeBase, entry_id: str) -> Entry:
    entry = kb.get(entry_id)
    if entry is None:
        raise _fail(f"no entry {entry_id!r} in {kb.slug}")
    return entry


@knowledge_app.command("list")
def knowledge_list(
    ctx: typer.Context,
    slug: str,
    all_entries: Annotated[bool, typer.Option("--all", help="Include retired and stale.")] = False,
) -> None:
    """A project's entries, most confirmed and helpful first."""
    _, kb, _, _ = _knowledge_base(ctx, slug)
    entries, problems = kb.load()
    shown = [e for e in entries if all_entries or e.active]
    shown.sort(key=lambda e: (not e.confirmed, -(e.helped - e.hurt), e.created))
    table = _table("id", "kind", "summary", "files", "status", "helped", "hurt")
    for e in shown:
        status = e.status if e.status != "active" else e.confidence
        table.add_row(
            e.id,
            e.kind,
            escape(e.summary),
            escape(", ".join(e.paths)),
            status,
            str(e.helped),
            str(e.hurt),
        )
    console.print(table)
    for problem in problems:
        err.print(f"[yellow]unreadable:[/yellow] {escape(problem.file)}: {escape(problem.reason)}")


@knowledge_app.command("show")
def knowledge_show(ctx: typer.Context, slug: str, entry_id: str) -> None:
    """One entry in full, with its history."""
    _, kb, _, _ = _knowledge_base(ctx, slug)
    entry = _entry(kb, entry_id)
    typer.echo(entry.to_text(), nl=False)
    for change in kb.history(entry_id):
        console.print(f"{change.date[:16]}  {escape(change.subject)}")


@knowledge_app.command("add")
def knowledge_add(
    ctx: typer.Context,
    slug: str,
    kind: Annotated[str, typer.Argument(help="convention, pitfall, command, decision or layout")],
    summary: str,
    body: Annotated[str, typer.Option(help="Two or three sentences.")] = "",
    path: Annotated[list[str] | None, typer.Option(help="A repository path it is about.")] = None,
) -> None:
    """Add an entry as a person. PRAT never rewrites what a person wrote."""
    from prat.knowledge.entries import Entry, new_id

    _, kb, _, _ = _knowledge_base(ctx, slug)
    entry = Entry(
        id=new_id(),
        kind=kind,
        summary=summary.strip(),
        body=body.strip(),
        paths=list(path or []),
        author="person",
        confidence="confirmed",
    )
    try:
        entry.validate()
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    kb.save(entry)
    kb.write_index()
    kb.commit(f"Added by a person: {entry.summary}")
    console.print(f"added {entry.id}")


@knowledge_app.command("confirm")
def knowledge_confirm(ctx: typer.Context, slug: str, entry_id: str) -> None:
    """Mark an entry as confirmed by a person: it is preferred when agents are briefed."""
    _, kb, _, _ = _knowledge_base(ctx, slug)
    entry = _entry(kb, entry_id)
    entry.confidence = "confirmed"
    kb.save(entry)
    kb.write_index()
    kb.commit(f"Confirmed by a person: {entry.summary}")
    console.print(f"confirmed {entry.id}")


@knowledge_app.command("retire")
def knowledge_retire(ctx: typer.Context, slug: str, entry_id: str) -> None:
    """Retire an entry that is wrong or no longer useful. It stays in the history."""
    from prat.knowledge.entries import now_iso

    _, kb, _, _ = _knowledge_base(ctx, slug)
    entry = _entry(kb, entry_id)
    entry.status = "retired"
    entry.invalid_at = now_iso()
    kb.save(entry)
    kb.write_index()
    kb.commit(f"Retired by a person: {entry.summary}")
    console.print(f"retired {entry.id}")


@knowledge_app.command("graph")
def knowledge_graph(
    ctx: typer.Context,
    slug: str,
    all_entries: Annotated[bool, typer.Option("--all", help="Include retired and stale.")] = False,
) -> None:
    """The project's knowledge graph as JSON: entries, files, links and imports."""
    import json

    from prat.knowledge.graph import build_graph, code_map_for

    _, kb, agent, branch = _knowledge_base(ctx, slug)
    code = code_map_for(kb, agent, branch)
    graph = build_graph(kb.entries(), code, include_inactive=all_entries, project=slug)
    typer.echo(json.dumps(graph.to_dict(), indent=2))


@knowledge_app.command("seed")
def knowledge_seed(
    ctx: typer.Context,
    slug: str,
    model: Annotated[str | None, typer.Option(help="Model for the scout.")] = None,
    quick: Annotated[
        bool, typer.Option("--quick", help="One short session instead of the deep scan.")
    ] = False,
    runtime: RuntimeOption = None,
    script: ScriptOption = None,
) -> None:
    """Map the project: the deep scan inventories every file with tools, reads every module and
    writes the project's knowledge. --quick is a single short look instead."""
    from prat.knowledge.deepscan import deep_seed
    from prat.knowledge.seed import seed_project

    with _database(ctx) as (settings, db):
        agent = OverlayGuidedRuntime(_make_runtime(settings, runtime, script), settings.home)
        chosen = model or resolve_model(Role.SCOUT, settings_models=settings.models)
        try:
            if quick:
                result = asyncio.run(seed_project(db, settings, agent, slug, model=chosen))
            else:
                console.print(f"deep scan of {escape(slug)}: inventory, every module, synthesis")
                reader = resolve_model(Role.READER, settings_models=settings.models)
                deep = asyncio.run(
                    deep_seed(db, settings, agent, slug, reader_model=reader, synth_model=chosen)
                )
                console.print(
                    f"{len(deep.added)} entries added; read {deep.files_read} of {deep.readable} "
                    f"files in {deep.modules} modules; timings {deep.timings}"
                )
                console.print(f"journal: {escape(deep.journal)}")
                if deep.error:
                    raise _fail(deep.error, 2)
                return
        except store.NotFound as exc:
            raise _fail(str(exc)) from exc
    console.print(f"{len(result.added)} entries added, {len(result.confirmed)} already known")
    for reason in result.rejected:
        console.print(f"[yellow]refused:[/yellow] {escape(reason)}")
    console.print(f"journal: {escape(result.journal)}")
    if result.error:
        raise _fail(result.error, 2)


@knowledge_app.command("reindex")
def knowledge_reindex(ctx: typer.Context, slug: str) -> None:
    """Rebuild the code map of the default branch and the MEMORY.md index."""
    from prat.knowledge.graph import code_map_for

    _, kb, agent, branch = _knowledge_base(ctx, slug)
    kb.ensure()
    code = code_map_for(kb, agent, branch)
    kb.write_index()
    kb.commit("Reindexed")
    if code is None:
        console.print("index rebuilt; no clone yet, so no code map (run a card first)")
    else:
        console.print(
            f"code map of {code.commit[:10]}: {len(code.files)} files, {len(code.edges)} imports"
        )


@dream_app.command("status")
def dream_status(ctx: typer.Context) -> None:
    """Whether PRAT is dreaming, the next dream window and the last dreams."""
    from prat.dream import recent_dreams

    with _database(ctx) as (_, db):
        current = rhythm.state(db)
        conf = rhythm.load_settings(db)
        dreams = recent_dreams(db, 5)
    console.print(f"state: {current.name} ({current.reason})")
    console.print(f"mode: {conf.mode}" + (f", time zone {conf.timezone}" if conf.timezone else ""))
    if current.window is not None:
        kind = "learned" if current.window.learned else "default"
        console.print(
            f"dream window ({kind}): {current.window.start:%a %H:%M} to {current.window.end:%H:%M}"
        )
    table = _table("dream", "trigger", "status", "phase", "started")
    for row in dreams:
        table.add_row(
            str(row.id), row.trigger, row.status, row.phase, f"{row.started_at:%Y-%m-%d %H:%M}"
        )
    console.print(table)


@dream_app.command("now")
def dream_now(
    ctx: typer.Context, runtime: RuntimeOption = None, script: ScriptOption = None
) -> None:
    """Dream now: tidy, bring project knowledge up to date, reflect. Waits for a person's
    running card to finish first."""
    from prat.dream import run_dream

    with _database(ctx) as (settings, db):
        agent = OverlayGuidedRuntime(_make_runtime(settings, runtime, script), settings.home)
        result = asyncio.run(
            run_dream(db, settings, agent, trigger="manual", runtime_name=agent.name, forced=True)
        )
    console.print(f"dream #{result.dream_id}: {result.status}")
    remembered = result.phases.get("remember") or {}
    for project in remembered.get("projects", []):
        console.print(
            f"  {project['slug']}: {project['ingested']} card run(s) read, "
            f"{project.get('settled', 0)} settled card(s) read again, "
            f"{project.get('checked', 0)} checked against the code "
            f"({project.get('check_wrong', 0)} no longer true, "
            f"{project.get('check_refined', 0)} brought up to date), "
            f"{len(project['changes'])} change(s), {project['helped']} credited, "
            f"{project['stale']} stale, {project['merged']} merged"
        )
        for change in project["changes"]:
            console.print(f"    {change}")
    if result.status != "succeeded":
        raise typer.Exit(2)


@dream_app.command("hours")
def dream_hours(
    ctx: typer.Context,
    mode: Annotated[str, typer.Argument(help="learn, fixed or never")],
    start: Annotated[int, typer.Option(help="Fixed: first quiet hour (0 to 23).")] = 1,
    end: Annotated[int, typer.Option(help="Fixed: hour the quiet ends (0 to 23).")] = 5,
    timezone: Annotated[str, typer.Option(help="An IANA time zone; empty is the system's.")] = "",
) -> None:
    """Choose when PRAT dreams: learned from your activity, fixed hours, or never."""
    with _database(ctx) as (_, db):
        try:
            rhythm.save_settings(db, rhythm.RhythmSettings(mode, timezone, start, end))
        except ValueError as exc:
            raise _fail(str(exc)) from exc
        current = rhythm.state(db)
    console.print(f"dream hours: {mode}; now {current.name} ({current.reason})")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
