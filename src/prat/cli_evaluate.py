"""``prat evaluate``: audit a project, read the report, turn it into work.

Kept apart from ``prat.cli`` like the other later commands; importing this module registers
them.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape

from prat import store
from prat.cli import _database, _fail, _settings, _table, app, console
from prat.evaluate import library, model, pages, remediate
from prat.evaluate.tools import NotRun

evaluate_app = typer.Typer(
    no_args_is_help=True,
    help="Evaluate a project (security, accessibility, performance...), report, remediate.",
)
app.add_typer(evaluate_app, name="evaluate")

Severity = Annotated[str, typer.Option(help="critical, high, medium, low or info.")]


def _severity(value: str) -> str:
    if value not in model.SEVERITIES:
        raise _fail(f"{value!r} is not a severity ({', '.join(model.SEVERITIES)})")
    return value


@evaluate_app.command("list")
def evaluate_list() -> None:
    """The evaluations there are."""
    table = _table("name", "evaluates", "needs")
    for item in library.LIBRARY.values():
        needs = {"code": "the code", "pages": "a running page", "both": "the code, a page, or both"}
        where = needs[item.needs] + (" (websites)" if item.website else "")
        table.add_row(item.name, f"{item.title}: {item.about}", where)
    console.print(table)
    groups = "; ".join(f"{name} = {', '.join(members)}" for name, members in library.GROUPS.items())
    console.print(escape(f"Groups: {groups}"))


@evaluate_app.command("setup")
def evaluate_setup(ctx: typer.Context) -> None:
    """Install the libraries that judge pages (axe-core and Lighthouse) with npm, into PRAT's
    own folder. Without them PRAT's own page checks stand in."""
    settings = _settings(ctx)
    try:
        pages.setup(settings.home)
    except NotRun as exc:
        raise _fail(str(exc)) from exc
    console.print(
        escape(f"Installed {', '.join(pages.PACKAGES)} in {pages.tools_dir(settings.home)}")
    )


def _visit_urls(urls: list[str]) -> library.Visit:
    def visit(page: Callable[[str], None]) -> None:
        for url in urls:
            page(url)

    return visit


def _visit_project(settings: Any, slug: str, spec: Any, base: Path) -> library.Visit | None:
    """Open the pages the project's file names, on its own environment started on ``base``."""
    wanted = spec.screens
    if wanted is None or not wanted.environment or not wanted.pages:
        return None
    from prat.engine import screencheck
    from prat.environments import EnvironmentManager, load_local
    from prat.sandbox import environment_sandboxes

    def visit(page: Callable[[str], None]) -> None:
        def opener(url: str, **_: Any) -> list[Any]:
            page(url)
            return []

        manager = EnvironmentManager(settings.home, environment_sandboxes(settings))
        looked = screencheck.look(
            manager,
            slug,
            spec,
            base,
            screencheck.BASELINE_CARD,
            load_local(settings.home, slug).variables,
            opener=opener,
        )
        if not looked.looked:
            raise NotRun(f"the project's pages could not be opened: {looked.reason}")

    return visit


@evaluate_app.command("run")
def evaluate_run(
    ctx: typer.Context,
    names: Annotated[
        list[str], typer.Argument(help="Evaluations or groups: security, accessibility, all...")
    ],
    project: Annotated[str | None, typer.Option("--project", "-p", help="A PRAT project.")] = None,
    path: Annotated[
        Path | None, typer.Option("--path", help="Any folder of code, a PRAT project or not.")
    ] = None,
    url: Annotated[
        list[str] | None, typer.Option("--url", help="A page to open; repeat for more.")
    ] = None,
    ignore: Annotated[
        list[str] | None,
        typer.Option("--ignore", help="A path glob to leave out (vendored code); repeat for more."),
    ] = None,
    fail_on: Annotated[
        str | None,
        typer.Option("--fail-on", help="Exit 1 when a finding is this severe or worse."),
    ] = None,
    floor: Severity = "low",
    as_json: Annotated[bool, typer.Option("--json", help="Print the report as JSON.")] = False,
) -> None:
    """Run evaluations and show the report. Give a project, a folder, pages, or a mix: what an
    evaluation needs and was not given is reported as not run. Nothing is changed and no card
    is made, unless the project's file says to remediate by itself."""
    try:
        wanted = library.resolve(names)
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    if fail_on is not None:
        _severity(fail_on)
    _severity(floor)
    if project is None and path is None and not url:
        raise _fail("give --project, --path or --url")
    settings = _settings(ctx)
    root = path.resolve() if path is not None else None
    slug = (root.name.lower() if root is not None else "site") if project is None else project
    spec = None
    commit = ""
    if project is not None:
        from prat.git import Git, GitError
        from prat.projects import ProjectFileError, load_spec

        with _database(ctx) as (_, db), db.session() as session:
            try:
                row = store.get_project(session, project)
            except store.NotFound as exc:
                raise _fail(str(exc)) from exc
            slug, agent = row.slug, Path(row.agent_path)
            # PRAT's own clone of the default branch; the person's checkout until it exists.
            # Evaluating only reads.
            if not agent.is_dir():
                agent = Path(row.checkout_path)
        root = root or agent
        try:
            spec = load_spec(agent, default_name=slug)
        except (ProjectFileError, OSError):
            spec = None
        try:
            commit = Git(root).head()
        except GitError:
            commit = ""
    if root is not None and not root.is_dir():
        raise _fail(f"{root} is not a folder")
    visit = _visit_urls(list(url)) if url else None
    if visit is None and spec is not None and root is not None:
        visit = _visit_project(settings, slug, spec, root)
    target = ", ".join(str(t) for t in (root, *(url or [])) if t)
    with console.status("evaluating") as status:
        try:
            report = library.run(
                settings.home,
                slug,
                wanted,
                root=root,
                visit=visit,
                target=target,
                commit=commit,
                ignore=[*(spec.evaluate.ignore if spec is not None else []), *(ignore or [])],
                said=lambda text: status.update(escape(text)),
            )
        except NotRun as exc:
            raise _fail(str(exc)) from exc
    saved = model.save(settings.home, report)
    compared = model.compare(settings.home, report)
    if as_json:
        console.print_json(json.dumps(report.to_dict()))
    else:
        text = model.render(report, compared, titles=library.TITLES, floor=floor)
        console.print(escape(text), highlight=False)
        console.print(escape(f"\nKept as {saved}"), highlight=False)
    urgent = model.worst_new(report, compared)
    if urgent and settings.alert_webhook:
        from prat import alerts

        alerts.send(
            settings.alert_webhook,
            f"{len(urgent)} critical finding(s) in {slug}: {urgent[0].title} ({urgent[0].where})",
            title="PRAT evaluation",
        )
    auto = spec.evaluate if spec is not None else None
    if auto is not None and auto.remediate and project is not None:
        with _database(ctx) as (_, db):
            _remediate(
                settings.home, db, slug, report, wanted, auto.floor, auto.max_cards, auto.hours
            )
    if fail_on is not None and any(model.at_least(f.severity, fail_on) for f in report.findings):
        raise typer.Exit(1)


@evaluate_app.command("show")
def evaluate_show(
    ctx: typer.Context,
    project: Annotated[
        str, typer.Argument(help="The project (or the name a folder was kept under).")
    ],
    report_id: Annotated[str | None, typer.Option("--report", help="A report's id.")] = None,
    floor: Severity = "low",
    every: Annotated[
        bool, typer.Option("--all", help="List every place, not the first few.")
    ] = False,
) -> None:
    """A kept report (the newest unless one is named), and the reports there are."""
    settings = _settings(ctx)
    report = model.load(settings.home, project, report_id)
    if report is None:
        raise _fail(f"no report for {project}; run `prat evaluate run`")
    text = model.render(
        report,
        model.compare(settings.home, report),
        titles=library.TITLES,
        floor=_severity(floor),
        shown=10_000 if every else model.SHOWN,
    )
    console.print(escape(text), highlight=False)
    known = model.ids(settings.home, project)
    console.print(escape(f"\nReports kept: {', '.join(known[-8:])}"), highlight=False)


def _remediate(
    home: Path,
    db: Any,
    slug: str,
    report: model.Report,
    names: list[str] | None,
    floor: str,
    max_cards: int,
    hours: float | None,
    *,
    start: bool = True,
) -> None:
    works = remediate.plan(
        report, floor=floor, evaluations=names, skip=remediate.open_keys(db, home, slug)
    )
    if not works:
        console.print(escape(f"Nothing at {floor} or worse without a card already."))
        return
    made = remediate.create(db, home, slug, works, report)
    console.print(
        escape(
            f"{len(made.cards)} cards in the backlog under "
            + ", ".join(
                f"#{epic} ({library.TITLES.get(name, name)})" for name, epic in made.epics.items()
            )
        )
    )
    if not start:
        console.print("Nothing started. Start cards yourself, or run again with --start.")
        return
    if hours:
        remediate.open_window(
            home, slug, list(made.epics.values()), hours=hours, max_cards=max_cards
        )
        ready = remediate.advance(db, home, slug)
        until = time.strftime("%H:%M", time.localtime(time.time() + hours * 3600))
        console.print(
            escape(
                f"Window open until {until}, up to {max_cards} cards, worst first. "
                f"Started: {', '.join(f'#{c}' for c in ready) or 'none yet'}. "
                "The server starts the next as each finishes."
            )
        )
    else:
        ready = remediate.start(db, made.cards[:max_cards], "remediation")
        console.print(escape(f"Started: {', '.join(f'#{c}' for c in ready)}"))


@evaluate_app.command("remediate")
def evaluate_remediate(
    ctx: typer.Context,
    project: Annotated[str, typer.Argument(help="The project.")],
    names: Annotated[
        list[str] | None, typer.Argument(help="Only these evaluations of the report.")
    ] = None,
    report_id: Annotated[str | None, typer.Option("--report", help="A report's id.")] = None,
    floor: Annotated[
        str | None, typer.Option(help="Make cards for findings this severe or worse.")
    ] = None,
    max_cards: Annotated[
        int | None, typer.Option("--max-cards", help="Start at most this many.")
    ] = None,
    hours: Annotated[
        float | None,
        typer.Option(help="Keep starting the next card as one finishes, for this long."),
    ] = None,
    start: Annotated[
        bool, typer.Option("--start/--no-start", help="Start work now, within the limits.")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the cards; make none.")] = False,
) -> None:
    """Turn a report into cards: one per kind of fault, worst first, as drafts in the backlog
    under one epic per evaluation. Nothing is started unless --start says so; with --hours the
    server works through them one after another until the time or the count runs out. Limits
    left out come from the project's [evaluate] table."""
    from prat.projects import Evaluate, ProjectFileError, load_spec

    with _database(ctx) as (settings, db):
        with db.session() as session:
            try:
                row = store.get_project(session, project)
            except store.NotFound as exc:
                raise _fail(str(exc)) from exc
            slug, agent = row.slug, Path(row.agent_path)
        try:
            defaults = load_spec(agent, default_name=slug).evaluate
        except (ProjectFileError, OSError):
            defaults = Evaluate()
        report = model.load(settings.home, slug, report_id)
        if report is None:
            raise _fail(f"no report for {slug}; run `prat evaluate run` first")
        try:
            wanted = library.resolve(names) if names else None
        except ValueError as exc:
            raise _fail(str(exc)) from exc
        level = _severity(floor or defaults.floor)
        cap = max_cards if max_cards is not None else defaults.max_cards
        if dry_run:
            works = remediate.plan(
                report,
                floor=level,
                evaluations=wanted,
                skip=remediate.open_keys(db, settings.home, slug),
            )
            table = _table("severity", "evaluation", "card", "places")
            for work in works:
                title = remediate.card_text(slug, work)[0]
                table.add_row(work.severity, work.evaluation, title, str(len(work.findings)))
            console.print(table)
            return
        _remediate(
            settings.home,
            db,
            slug,
            report,
            wanted,
            level,
            cap,
            hours if hours is not None else (defaults.hours if start else None),
            start=start,
        )


@evaluate_app.command("window")
def evaluate_window(
    ctx: typer.Context,
    project: Annotated[str, typer.Argument(help="The project.")],
    close: Annotated[bool, typer.Option("--close", help="Stop starting new cards.")] = False,
) -> None:
    """The project's open remediation window, if it has one. --close ends it; cards already
    started finish."""
    settings = _settings(ctx)
    window = remediate.load_window(settings.home, project)
    if window is None:
        console.print("No window is open.")
        return
    if close:
        remediate.close_window(settings.home, project)
        console.print("Closed. Cards already started will finish; no more will start.")
        return
    until = time.strftime("%Y-%m-%d %H:%M", time.localtime(window.until))
    console.print(
        escape(
            f"Open until {until}: {len(window.started)} of at most {window.max_cards} cards "
            f"started ({', '.join(f'#{c}' for c in window.started) or 'none'})."
        )
    )
