"""Command line for jobs, skills, the crew (and each agent's memory) and epics.

Registered on PRAT's main command (prat.cli) when it is imported.
"""

from __future__ import annotations

import time
from typing import Annotated, Any

import typer
from rich.markup import escape

from prat import store
from prat.cli import _database, _fail, _settings, _table, app, console

job_app = typer.Typer(no_args_is_help=True, help="Long-running jobs: start, watch, land.")
skill_app = typer.Typer(no_args_is_help=True, help="Skills: loadouts, candidates, scorecards.")
crew_app = typer.Typer(no_args_is_help=True, help="The crew and each agent's memory.")
epic_app = typer.Typer(no_args_is_help=True, help="Epics and their parts.")
app.add_typer(job_app, name="job")
app.add_typer(skill_app, name="skill")
app.add_typer(crew_app, name="crew")
app.add_typer(epic_app, name="epic")


# Jobs -----------------------------------------------------------------------------------


@job_app.command("list")
def job_list(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option(help="Only this project.")] = None,
) -> None:
    """Recent job runs with their progress, rate and ETA."""
    from prat import jobs

    with _database(ctx) as (_, db):
        try:
            found = jobs.states(db, slug=project)
        except store.NotFound as exc:
            raise _fail(str(exc)) from exc
    table = _table("run", "project", "job", "status", "progress")
    for state in found:
        table.add_row(
            str(state.run_id), state.slug, state.title, state.status, escape(jobs.summary(state))
        )
    console.print(table)


@job_app.command("start")
def job_start(ctx: typer.Context, project: str, name: str) -> None:
    """Start a job from the project file, in your checkout."""
    from prat import jobs

    with _database(ctx) as (settings, db):
        try:
            run_id = jobs.start(settings, db, project, name)
        except (jobs.JobError, LookupError) as exc:
            raise _fail(str(exc)) from exc
    console.print(f"job {name} started as run {run_id}; watch it with: prat job show {run_id}")


@job_app.command("show")
def job_show(ctx: typer.Context, run_id: int) -> None:
    """One job run: each phase with its rate and ETA, errors and the end of its log."""
    from prat import jobs

    with _database(ctx) as (_, db):
        state = next((s for s in jobs.states(db, limit=500) if s.run_id == run_id), None)
    if state is None:
        raise _fail(f"no job run {run_id}")
    console.print(escape(jobs.summary(state)))
    for phase in state.progress.phases:
        total = f" of {phase.total:.0f}" if phase.total else ""
        rate = f", {phase.rate * 60:.1f}/min" if phase.rate else ""
        eta = (
            f", ETA {jobs.clock(phase.eta_low)} to {jobs.clock(phase.eta_high)}"
            if phase.eta_low is not None
            else ""
        )
        console.print(f"  {escape(phase.name)}: {phase.done:.0f}{total} {phase.unit}{rate}{eta}")
    if state.log_tail:
        console.print(escape(state.log_tail))


@job_app.command("stop")
def job_stop(ctx: typer.Context, run_id: int) -> None:
    """Stop a running job and everything it started."""
    from prat import jobs

    with _database(ctx) as (_, db):
        try:
            jobs.stop(db, run_id)
        except jobs.JobError as exc:
            raise _fail(str(exc)) from exc
    console.print(f"job run {run_id} stopped")


@job_app.command("land")
def job_land(ctx: typer.Context, run_id: int) -> None:
    """Merge a finished job's own database branch into the default branch."""
    from prat import jobs

    with _database(ctx) as (settings, db):
        try:
            merged = jobs.land(settings, db, run_id)
        except jobs.JobError as exc:
            raise _fail(str(exc)) from exc
    console.print(f"landed: {merged}")


@job_app.command("discard")
def job_discard(ctx: typer.Context, run_id: int) -> None:
    """Throw away a finished job's own database branch."""
    from prat import jobs

    with _database(ctx) as (settings, db):
        try:
            jobs.discard(settings, db, run_id)
        except jobs.JobError as exc:
            raise _fail(str(exc)) from exc
    console.print(f"job run {run_id}'s data discarded")


# Skills ----------------------------------------------------------------------------------


@skill_app.command("list")
def skill_list(ctx: typer.Context, project: str) -> None:
    """The skills a project may load, what its lead carries, candidates and scorecards."""
    from prat import crew, skills
    from prat.environments import committed_spec

    with _database(ctx) as (settings, db):
        try:
            checkout, spec, _problem = committed_spec(db, project)
        except LookupError as exc:
            raise _fail(str(exc)) from exc
        cards = skills.scorecards(db, settings.home, project)
    member = crew.lead_for(spec.crew.model_dump(), checkout)
    carried = set(crew.loadout(member, spec.crew.model_dump(), settings.home, project))
    table = _table("skill", "tier", "phase", "carried", "loads", "first review", "advice")
    for name, path in crew.available_skills(settings.home, project).items():
        meta = crew.skill_meta(path / "SKILL.md")
        card = cards.get(name)
        rate = card.first_review_rate if card else None
        table.add_row(
            name,
            meta.get("tier", "project"),
            meta.get("phase", "any"),
            "yes" if name in carried else "",
            str(card.loads) if card else "0",
            f"{rate:.0%}" if rate is not None else "",
            card.advice if card else "",
        )
    console.print(f"lead: {member.name} ({member.stack})")
    console.print(table)
    waiting = skills.candidates(settings.home, project)
    if waiting:
        console.print("candidates waiting for you: " + ", ".join(c.name for c in waiting))


@skill_app.command("promote")
def skill_promote(ctx: typer.Context, project: str, name: str) -> None:
    """Promote a candidate: the project's agents carry it from their next run."""
    from prat import skills

    try:
        skills.promote(_settings(ctx).home, project, name)
    except skills.SkillError as exc:
        raise _fail(str(exc)) from exc
    console.print(f"{name} promoted for {project}")


@skill_app.command("reject")
def skill_reject(ctx: typer.Context, project: str, name: str) -> None:
    """Reject a candidate (kept under rejected/, never loaded)."""
    from prat import skills

    try:
        skills.reject(_settings(ctx).home, project, name)
    except skills.SkillError as exc:
        raise _fail(str(exc)) from exc
    console.print(f"{name} rejected")


# The crew and memory ----------------------------------------------------------------------


@crew_app.command("list")
def crew_list(ctx: typer.Context) -> None:
    """Every agent with a memory: episodes, beliefs, how its work turned out, recent mood."""
    from prat import crew, identity, memory

    home = _settings(ctx).home
    table = _table("name", "role", "episodes", "beliefs", "landed", "sent back", "mood")
    for agent in memory.all_agents(home):
        stats = memory.AgentMemory(home, agent).stats()
        member = crew.MEMBERS.get(agent)
        outcomes = stats["outcomes"]
        table.add_row(
            identity.of(home, agent).name,
            f"lead, {member.stack}" if member else agent,
            str(stats["episodes"]),
            str(stats["beliefs"]),
            str(outcomes.get("landed", 0)),
            str(outcomes.get("reworked", 0)),
            f"{stats['mood']:+.2f}",
        )
    console.print(table)


@crew_app.command("memory")
def crew_memory(
    ctx: typer.Context,
    agent: str,
    query: Annotated[str, typer.Option(help="Recall the episodes that best match this.")] = "",
    limit: Annotated[int, typer.Option(help="How many episodes.")] = 10,
) -> None:
    """An agent's beliefs and episodes: what it did, how it reasoned and felt, how it ended."""
    from prat import identity
    from prat.memory import AgentMemory

    home = _settings(ctx).home
    key = _agent_key(ctx, agent)
    who = identity.of(home, key)
    console.print(f"[bold]{escape(who.name)}[/bold], {escape(who.role)}")
    if who.chosen:
        console.print(escape(f"{who.bio} ({who.personality})"))
        if who.drive:
            console.print(escape(f"Drive: {who.drive}"))
    memory = AgentMemory(home, key)
    beliefs = sorted(memory.beliefs(), key=lambda b: b.strength, reverse=True)
    if beliefs:
        console.print("[bold]Beliefs[/bold]")
        for belief in beliefs:
            scope = f" ({belief.project})" if belief.project else ""
            console.print(f"  {belief.strength:.1f}{escape(scope)} {escape(belief.text)}")
    episodes = memory.recall(query, limit=limit) if query else memory.episodes()[-limit:][::-1]
    console.print("[bold]Episodes[/bold]" if episodes else "No episodes yet.")
    for e in episodes:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.at))
        title = f"  {when} {e.project} #{e.card_id} {e.title} [{e.outcome}]"
        console.print(escape(title))
        for label, text in (
            ("did", e.what),
            ("reasoned", e.reasoning),
            ("learned", e.lesson),
            ("feedback", e.feedback),
        ):
            if text:
                console.print(f"    {label}: {escape(text[:300])}")
        console.print(f"    felt: {escape(e.feeling.words)} ({e.feeling.valence:+.1f})")


# Epics ---------------------------------------------------------------------------------------


@epic_app.command("list")
def epic_list(ctx: typer.Context) -> None:
    """Every epic and its parts: landed, running, waiting."""
    from prat import epics

    with _database(ctx) as (_, db):
        found = epics.views(db)
    if not found:
        console.print("No epics. Make one with: prat card add <project> <title> --epic")
    for view in found:
        console.print(
            f"[bold]#{view.id}[/bold] {escape(view.title)} ({view.project}): "
            f"{view.landed} of {len(view.parts)} landed"
        )
        for part in view.parts:
            waits = ", ".join(f"#{d}" for d in part["depends_on"])
            needs = f" waits on {waits}" if waits else ""
            console.print(f"   #{part['id']} {escape(part['title'])} [{part['status']}]{needs}")


# Talking with the crew -------------------------------------------------------------------------


def _through_server(settings: Any, action: str, body: dict[str, Any]) -> tuple[bool, str] | None:
    """Ask the running server to act (its runtime is signed in); None when none runs."""
    import httpx

    from prat.web.tokens import read_access_token

    token = read_access_token(settings)
    if not token:
        return None
    host = settings.web_host
    shown = f"[{host}]" if ":" in host else host
    try:
        response = httpx.post(
            f"http://{shown}:{settings.web_port}/api/act/{action}",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=300,
        )
    except httpx.HTTPError:
        return None
    try:
        data = response.json()
    except ValueError:
        return (False, f"the server answered {response.status_code}")
    if response.status_code == 200:
        return (True, str(data.get("message") or ""))
    return (False, str(data.get("error") or response.status_code))


def _locally(ctx: typer.Context, action: str, body: dict[str, Any]) -> str:
    import asyncio

    from prat import converse, identity
    from prat.model_registry import Role, resolve_model
    from prat.runtime.claude import ClaudeRuntime

    with _database(ctx) as (settings, db):
        runtime = ClaudeRuntime.from_settings(settings)
        model = resolve_model(Role.CREW, settings_models=settings.models)
        if action == "talk":
            return asyncio.run(
                converse.talk(
                    settings.home, db, runtime, body["agent"], body["message"], model=model
                )
            )
        if action == "introduce":
            who = asyncio.run(
                identity.introduce(settings.home, runtime, body["agent"], model=model)
            )
            return f"{who.name}: {who.bio}"
        reports = asyncio.run(converse.standup(settings.home, db, runtime, model=model))
        return "\n\n".join(f"{r.name}\n{r.text}" for r in reports) or "Nobody worked today."


def _converse(ctx: typer.Context, action: str, body: dict[str, Any]) -> None:
    sent = _through_server(_settings(ctx), action, body)
    if sent is None:
        text = _locally(ctx, action, body)
    else:
        ok, text = sent
        if not ok:
            raise _fail(text)
    console.print(escape(text))


def _agent_key(ctx: typer.Context, agent: str) -> str:
    """An agent by its key (python, scout, critic) or by the name it chose."""
    from prat import identity, memory

    home = _settings(ctx).home
    wanted = agent.strip().lower()
    for key in memory.all_agents(home):
        if key == wanted or identity.of(home, key).name.lower() == wanted:
            return key
    from prat import crew

    if wanted in crew.MEMBERS or wanted in crew.HELPER_PROMPTS or wanted in identity.BENCH:
        return wanted
    raise _fail(f"no agent called {agent!r}; see prat crew list")


@crew_app.command("talk")
def crew_talk(ctx: typer.Context, agent: str, message: str) -> None:
    """Say something to an agent (by its name or role) and read its answer. It never
    interrupts the agent's work."""
    _converse(ctx, "talk", {"agent": _agent_key(ctx, agent), "message": message})


@crew_app.command("standup")
def crew_standup(ctx: typer.Context) -> None:
    """Every agent that worked in the last day reports: done, doing, blockers, how they feel."""
    _converse(ctx, "standup", {})


@crew_app.command("introduce")
def crew_introduce(ctx: typer.Context, agent: str) -> None:
    """Ask an agent to choose its name, bio and personality (again)."""
    _converse(ctx, "introduce", {"agent": _agent_key(ctx, agent)})


@crew_app.command("inherit")
def crew_inherit(
    ctx: typer.Context,
    project: Annotated[str | None, typer.Option(help="Only this project.")] = None,
    dry_run: Annotated[bool, typer.Option(help="Show what would be added; add nothing.")] = False,
) -> None:
    """Give the crew PRAT's record of the work done before they kept memories: each run,
    how it turned out and your feedback, marked as inherited. Then each agent forms beliefs
    from it. Running it again adds only what is new."""
    from prat import inherit

    if not dry_run:
        sent = _through_server(_settings(ctx), "inherit", {"project": project or ""})
        if sent is not None:
            ok, text = sent
            if not ok:
                raise _fail(text)
            console.print(escape(text))
            return
    with _database(ctx) as (settings, db):
        try:
            found = inherit.inherit(db, settings.home, project=project, dry_run=dry_run)
        except LookupError as exc:
            raise _fail(str(exc)) from exc
    console.print(escape(inherit.describe(settings.home, found)))
    if dry_run:
        console.print("Nothing was added (dry run).")
    else:
        console.print("PRAT is not running here: beliefs form at its next dream.")
