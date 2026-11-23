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
idea_app = typer.Typer(no_args_is_help=True, help="The crew's ideas for working better.")
ledger_app = typer.Typer(no_args_is_help=True, help="The ledger: which settings suit which work.")
coach_app = typer.Typer(no_args_is_help=True, help="The coach: slips, rules and attention.")
screens_app = typer.Typer(no_args_is_help=True, help="Screens: layout faults found from the page.")
app.add_typer(job_app, name="job")
app.add_typer(skill_app, name="skill")
app.add_typer(crew_app, name="crew")
app.add_typer(epic_app, name="epic")
app.add_typer(idea_app, name="idea")
app.add_typer(ledger_app, name="ledger")
app.add_typer(coach_app, name="coach")
app.add_typer(screens_app, name="screens")


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


# Ideas ---------------------------------------------------------------------------------------


@idea_app.command("list")
def idea_list(
    ctx: typer.Context,
    status: Annotated[
        str | None, typer.Option(help="proposed, accepted, declined, in_place or withdrawn.")
    ] = None,
) -> None:
    """The crew's ideas, newest first: waiting ones first unless you pick a status."""
    from prat import ideas, identity

    home = _settings(ctx).home
    found = ideas.all_ideas(home)
    if status:
        found = [i for i in found if i.status == status]
    else:
        found = sorted(found, key=lambda i: i.status != "proposed")
    if not found:
        console.print("No ideas yet. Agents propose them while they work and while PRAT dreams.")
        return
    table = _table("id", "status", "from", "kind", "project", "title")
    for idea in found:
        table.add_row(
            idea.id,
            idea.status.replace("_", " "),
            identity.of(home, idea.agent).name,
            idea.kind,
            idea.project or "",
            escape(idea.title),
        )
    console.print(table)


@idea_app.command("show")
def idea_show(ctx: typer.Context, idea_id: str) -> None:
    """One idea in full: the problem, the proposal, its evidence and the decision."""
    from prat import ideas, identity

    home = _settings(ctx).home
    try:
        idea = ideas.get(home, idea_id)
    except ideas.IdeaError as exc:
        raise _fail(str(exc)) from exc
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(idea.at))
    origin = f"card {idea.card_id}" if idea.card_id else "a dream"
    console.print(
        f"[bold]{escape(idea.title)}[/bold]  ({idea.id}, {idea.status.replace('_', ' ')})"
    )
    console.print(
        escape(f"{identity.of(home, idea.agent).name}, {when}, from {origin}; kind {idea.kind}")
        + (escape(f", project {idea.project}") if idea.project else "")
    )
    for label, text in (
        ("Problem", idea.problem),
        ("Proposal", idea.proposal),
        ("Benefit", idea.benefit),
        ("Evidence", ", ".join(idea.evidence)),
        ("Decision note", idea.note),
    ):
        if text:
            console.print(f"[bold]{label}[/bold]\n{escape(text)}")
    if idea.work_card:
        console.print(f"Being put in place by card {idea.work_card}.")


def _idea_act(ctx: typer.Context, action: str, body: dict[str, Any]) -> None:
    from prat import ideas

    sent = _through_server(_settings(ctx), action, body)
    if sent is not None:
        ok, text = sent
        if not ok:
            raise _fail(text)
        console.print(escape(text))
        return
    with _database(ctx) as (settings, db):
        try:
            idea = ideas.decide(
                settings.home,
                body["idea"],
                accept=action == "idea-accept",
                note=body.get("note") or "",
            )
            text = f"{idea.title}: {idea.status}"
            if action == "idea-accept" and body.get("build"):
                card = ideas.put_in_place(
                    db, settings.home, idea.id, project=body.get("project") or None
                )
                text += f"; card {card} puts it in place (run it with prat card run {card})"
        except (ideas.IdeaError, LookupError) as exc:
            raise _fail(str(exc)) from exc
    console.print(escape(text))


@idea_app.command("accept")
def idea_accept(
    ctx: typer.Context,
    idea_id: str,
    note: Annotated[str, typer.Option(help="What you think; its author remembers it.")] = "",
    build: Annotated[
        bool, typer.Option(help="Make a card that puts it in place, and run it.")
    ] = False,
    project: Annotated[
        str | None, typer.Option(help="The project that puts it in place, if not its own.")
    ] = None,
) -> None:
    """Accept an idea. With --build a card on its project puts it in place."""
    body = {"idea": idea_id, "note": note, "build": build, "project": project or ""}
    _idea_act(ctx, "idea-accept", body)


@idea_app.command("decline")
def idea_decline(
    ctx: typer.Context,
    idea_id: str,
    note: Annotated[str, typer.Option(help="Why; its author learns from it.")],
) -> None:
    """Decline an idea, saying why."""
    _idea_act(ctx, "idea-decline", {"idea": idea_id, "note": note})


@idea_app.command("gather")
def idea_gather(ctx: typer.Context) -> None:
    """Ask every agent with new work since it last looked back for ideas now, as a dream
    would. Runs in the server, whose runtime is signed in."""
    sent = _through_server(_settings(ctx), "ideate", {})
    if sent is None:
        raise _fail("PRAT's server is not running here; ideas are gathered in its dreams.")
    ok, text = sent
    if not ok:
        raise _fail(text)
    console.print(escape(text))


# The social hour -------------------------------------------------------------------------------


@crew_app.command("social")
def crew_social(
    ctx: typer.Context,
    show: Annotated[
        int, typer.Option(help="Show the last N social hours instead of holding one.")
    ] = 0,
) -> None:
    """Hold a social hour now (it also follows every dream), or read past ones with --show."""
    from prat import social

    if show:
        home = _settings(ctx).home
        found = social.gatherings(home, limit=show)
        if not found:
            console.print("No social hour yet. One follows every dream.")
        for gathering in reversed(found):
            when = time.strftime("%A %Y-%m-%d %H:%M", time.localtime(gathering.started_at))
            console.print(f"[bold]{when}[/bold]")
            console.print(escape(gathering.transcript(home)))
            console.print("")
        return
    sent = _through_server(_settings(ctx), "social", {})
    if sent is None:
        raise _fail("PRAT's server is not running here; the social hour follows its dreams.")
    ok, text = sent
    if not ok:
        raise _fail(text)
    console.print(escape(text))


@crew_app.command("hello")
def crew_hello(
    ctx: typer.Context,
    message: str,
    speaker: Annotated[
        str | None,
        typer.Option("--as", help="Who is speaking, when it is not you (an assistant)."),
    ] = None,
) -> None:
    """Say good morning at the social hour; colleagues you name (or a few there) answer."""
    body = {"message": message, "speaker": speaker or ""}
    sent = _through_server(_settings(ctx), "social-hello", body)
    if sent is None:
        raise _fail("PRAT's server is not running here.")
    ok, text = sent
    if not ok:
        raise _fail(text)
    console.print(escape(text))


# Continuity ------------------------------------------------------------------------------------


@crew_app.command("story")
def crew_story(
    ctx: typer.Context,
    agent: Annotated[str | None, typer.Argument(help="One agent, by name or role.")] = None,
    write: Annotated[
        bool, typer.Option(help="Have it written (or revised) now, as a dream would.")
    ] = False,
) -> None:
    """An agent's own story and how its aspirations have changed; --write has it written now."""
    from prat import continuity, identity, memory

    key = _agent_key(ctx, agent) if agent else None
    if write:
        body: dict[str, Any] = {"agent": key or "", "force": bool(key)}
        sent = _through_server(_settings(ctx), "story", body)
        if sent is None:
            raise _fail("PRAT's server is not running here; stories are written in its dreams.")
        ok, text = sent
        if not ok:
            raise _fail(text)
        console.print(escape(text))
        if not key:
            return
    home = _settings(ctx).home
    for one in [key] if key else []:
        story = continuity.story_of(home, one)
        console.print(f"[bold]{escape(identity.of(home, one).name)}[/bold]")
        if not story.text:
            console.print("Has not written a story yet.")
            continue
        when = time.strftime("%Y-%m-%d", time.localtime(story.written_at))
        console.print(escape(f"(version {story.version}, {when})\n{story.text}"))
        for entry in continuity.aspirations_log(home, one)[-5:]:
            at = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(entry["at"])))
            console.print(escape(f"  {at} ({entry['source']}): " + "; ".join(entry["items"])))
    if not key:
        table = _table("agent", "story", "written", "aspirations logged")
        for one in sorted(memory.all_agents(home)):
            story = continuity.story_of(home, one)
            table.add_row(
                identity.of(home, one).name,
                f"version {story.version}" if story.text else "",
                time.strftime("%Y-%m-%d", time.localtime(story.written_at)) if story.text else "",
                str(len(continuity.aspirations_log(home, one))),
            )
        console.print(table)


@crew_app.command("continuity")
def crew_continuity(ctx: typer.Context) -> None:
    """Does continuity help? First-review landings since it started, with and without it."""
    from prat import continuity, identity

    settings = _settings(ctx)
    since, groups = continuity.compare(settings.home, settings.continuity, settings.continuity_with)
    if not since:
        console.print(f"continuity = {settings.continuity!r}; no work has opened with it yet.")
        return
    started = time.strftime("%Y-%m-%d %H:%M", time.localtime(since))
    console.print(f"continuity = {settings.continuity!r}, since {started}")
    table = _table("group", "agents", "landed", "first review", "rate", "sent back")
    for group in groups:
        rate = group.first_rate
        table.add_row(
            group.name,
            ", ".join(identity.of(settings.home, k).name for k in group.agents[:8])
            + ("…" if len(group.agents) > 8 else ""),
            str(group.landed),
            str(group.first),
            f"{rate:.0%}" if rate is not None else "",
            str(group.sent_back),
        )
    console.print(table)


# The ledger ------------------------------------------------------------------------------------


def _ledger_entries(settings: Any, db: Any) -> list[Any]:
    from prat import ledger
    from prat.git import Git

    scales: dict[tuple[str, str], int | None] = {}

    def scale(run: Any) -> int | None:
        clone, commit = run.card.project.agent_path, run.base_commit
        if not clone or not commit:
            return None
        if (clone, commit) not in scales:
            listed = Git(clone).run("ls-tree", "-r", "--name-only", commit, check=False)
            scales[(clone, commit)] = len(listed.stdout.splitlines()) if listed.ok else None
        return scales[(clone, commit)]

    def added_lines(run: Any) -> str | None:
        clone = run.card.project.agent_path
        if not clone or not run.base_commit or not run.head_commit:
            return None
        diff = Git(clone).run(
            "diff", "--unified=0", f"{run.base_commit}..{run.head_commit}", check=False
        )
        if not diff.ok:
            return None
        return "\n".join(line[1:] for line in diff.stdout.splitlines() if line.startswith("+"))

    return ledger.build(db, settings.home, scale=scale, added_lines=added_lines)


@ledger_app.command("export")
def ledger_export(ctx: typer.Context) -> None:
    """Write the ledger to a file: one line per run, with no client, project, path or text."""
    from prat import ledger

    with _database(ctx) as (settings, db):
        entries = _ledger_entries(settings, db)
    path = ledger.export(entries, settings.home)
    console.print(f"{len(entries)} runs written to {path}")


@ledger_app.command("show")
def ledger_show(
    ctx: typer.Context,
    by: Annotated[
        str,
        typer.Option(help="What to group the work by, e.g. area,work_type,change_size."),
    ] = "area,work_type",
    files: Annotated[
        list[str] | None,
        typer.Option("--file", help="Read exported ledgers (several machines) instead."),
    ] = None,
) -> None:
    """For each kind of work: the settings it was built with, best first."""
    from pathlib import Path

    from prat import ledger

    if files:
        entries = ledger.load(Path(f) for f in files)
    else:
        with _database(ctx) as (settings, db):
            entries = _ledger_entries(settings, db)
    names = [n.strip() for n in by.split(",") if n.strip()]
    table = _table(*names, "setting", "runs", "right", "median time", "median cost", "sent back")
    for row in ledger.table(entries, names):
        table.add_row(
            *row.work,
            row.setting,
            str(row.runs),
            f"{row.right} ({row.right_share:.0%})",
            f"{row.seconds:.0f} s" if row.seconds is not None else "",
            f"${row.cost_usd:.2f}",
            str(row.sent_back_by_person),
        )
    console.print(table)


# The coach -------------------------------------------------------------------------------------


@coach_app.command("show")
def coach_show(ctx: typer.Context) -> None:
    """Each project as the coach sees it: how its recent runs went, how closely it is watched,
    and the rules its next builder is given."""
    from prat import coach

    with _database(ctx) as (settings, db), db.session() as session:
        slugs = [project.slug for project in store.list_projects(session)]
    for slug in slugs:
        book = coach.load(settings.home, slug)
        if not book.runs:
            continue
        share = coach.clean_share(book)
        console.print(
            f"[bold]{escape(slug)}[/bold]: {len(book.runs)} runs read, "
            f"{share:.0%} of the last {len(book.recent)} clean, attention {coach.attention(book)}"
        )
        slips = coach.slip_counts(book.recent)
        if slips:
            console.print("   slips: " + ", ".join(f"{k} in {n} runs" for k, n in slips.items()))
        for rule in coach.standing(book):
            console.print(
                f"   - {escape(rule.text)}"
                + (f" ({escape(rule.evidence)})" if rule.evidence else "")
            )


@screens_app.command("check")
def screens_check(
    urls: Annotated[list[str], typer.Argument(help="The pages to open.")],
    widths: Annotated[
        str, typer.Option("--widths", help="Screen widths in pixels, comma separated.")
    ] = "390,768,1280",
    wait_ms: Annotated[
        int, typer.Option("--wait-ms", help="How long to let each page settle.")
    ] = 500,
    sets: Annotated[
        str,
        typer.Option(
            "--sets",
            help="Standard sets to hold the page to, comma separated: design, accessibility, "
            "performance, seo, llm, or all.",
        ),
    ] = "design",
) -> None:
    """Open each page at each width and list what is broken on it: controls covered or on top
    of each other, text cut off, sideways scrolling, targets too small, text too faint. With
    --sets, also what the page fails of the published accessibility, performance, search and
    AI search rules, and how it loaded. Exits 1 when anything is found, so it can be one of
    a project's gates."""
    from prat import pagecheck, screens

    sizes = [int(part) for part in widths.split(",") if part.strip()]
    asked = {part.strip().lower() for part in sets.split(",") if part.strip()}
    wanted = pagecheck.SETS if "all" in asked else tuple(s for s in pagecheck.SETS if s in asked)
    unknown = asked - set(pagecheck.SETS) - {"all"}
    if unknown or not wanted:
        console.print(f"[red]Unknown sets: {escape(', '.join(sorted(unknown)) or sets)}[/red]")
        raise typer.Exit(2)
    bad = False
    for url in urls:
        try:
            checked = pagecheck.check_page(url, widths=sizes, wait_ms=wait_ms, sets=wanted)
        except RuntimeError as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(2) from exc
        # With nothing to compare the load with, a slow timing is reported as it stands.
        found = checked.faults + pagecheck.slow(checked.metrics, None, wanted)
        bad = bad or bool(found)
        what = "Layout faults" if wanted == ("design",) else "Faults"
        console.print(
            escape(screens.report(found, url, said=pagecheck.SAID, what=what)), highlight=False
        )
        if checked.metrics and "performance" in wanted:
            console.print(
                escape("\nHow it loaded, once, here:\n" + pagecheck.table(checked.metrics))
            )
    if bad:
        raise typer.Exit(1)
