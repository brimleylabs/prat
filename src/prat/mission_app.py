"""``prat mission``: mission control in the terminal, next to the web UI.

One screen, five panels, redrawn every two seconds from :class:`prat.mission.MissionData`:

* **In flight**: each card an agent is working on, on its route (prepare, plan, build,
  verify, review, summary, finish) with the time spent on each leg, and the last things its
  agent did. Cards waiting for a slot follow.
* **Needs you**: an agent's question first, then cards to approve with the reviewer's verdict,
  the gates, the data they change and their lesson, then failures.
* **Fleet**: every project with its client, its databases and the state of each environment.
* **Guardrails**: what the policy refused in the last day, newest first.
* **Pulse**: agent minutes by hour, runs, cards landed, lessons written and the dream window.

Keys: ``j``/``k`` move between cards, ``enter`` follows one, ``a`` approves a card in review
(after a confirmation), ``o`` opens it in the web UI, ``c`` switches client, ``q`` quits. It
only reads, except for approve, which merges exactly as ``prat card approve`` does.
"""

from __future__ import annotations

import asyncio
import time
import webbrowser
from datetime import UTC, datetime
from typing import Any, ClassVar

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, OptionList, Static

from prat import crew, governor, skills
from prat import mission_screens as screens
from prat.config import Settings
from prat.db import Database
from prat.mission import (
    Flight,
    Leg,
    MissionData,
    RemoteDeck,
    Snapshot,
    Waiting,
    fetch_remote,
    sparkline,
)

REFRESH_SECONDS = 2.0
SLOW_SECONDS = 15.0
VIEWS = ("crew", "jobs", "epics", "machine", "skills", "usage")
VIEW_CSS = " ".join(
    f"Screen.view-{v} #grid {{ display: none; }} Screen.view-{v} #view-{v} {{ display: block; }}"
    for v in VIEWS
)
VIEW_TITLES = {
    "crew": "crew · who is working, what they remember, how they feel",
    "jobs": "jobs · long-running work",
    "epics": "epics · features in parts",
    "machine": "machine · what PRAT is running on this computer",
    "skills": "skills · what the crew carries and whether it helps",
    "usage": "usage · models and prompt cache",
}

# PRAT's dark theme (web/static/app.css): navy, with amber for what is moving.
ACCENT = "#f6b73c"
OK = "#4ade80"
WARN = "#fb923c"
BAD = "#f87171"
MUTED = "#9aa6bc"
FAINT = "#5d6a85"
TEXT = "#eef2f8"

LEG_STYLE = {
    "done": (OK, "●"),
    "running": (ACCENT, "◉"),
    "failed": (BAD, "✕"),
    "skipped": (FAINT, "◌"),
    "pending": (FAINT, "○"),
}
WAITING_MARK = {
    "blocked": (WARN, "?", "asks"),
    "review": (ACCENT, "◆", "review"),
    "failed": (BAD, "✕", "failed"),
}
VERDICT_STYLE = {"met": OK, "partial": WARN, "not_met": BAD, "wrong": BAD}
ENV_STYLE = {"running": OK, "starting": ACCENT, "unhealthy": WARN, "exited": BAD}
CATEGORY_MARK = {"tool": ("▸", MUTED), "text": ("»", TEXT), "denied": ("⛨", BAD)}


def clock(seconds: float | None) -> str:
    if seconds is None:
        return ""
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def route_text(route: list[Leg], *, timings: bool = False) -> Text:
    """The card's route as a rail: legs done, the one running, the ones still ahead."""
    text = Text()
    for index, leg in enumerate(route):
        colour, mark = LEG_STYLE[leg.state]
        if index:
            # Solid where the card has travelled: from a leg behind it to one done or under way.
            travelled = route[index - 1].state in ("done", "skipped") and leg.state != "pending"
            text.append(" ━ " if travelled else " ─ ", style=OK if travelled else FAINT)
        label = leg.name.upper() if leg.state == "running" else leg.name
        style = f"bold {colour}" if leg.state == "running" else colour
        text.append(f"{mark} {label}", style=style)
        if timings and leg.seconds is not None and leg.state != "pending":
            text.append(f" {clock(leg.seconds)}", style=FAINT)
    return text


def flight_block(
    flight: Flight, now: datetime, *, selected: bool, header_only: bool = False
) -> RenderableType:
    head = Text()
    head.append("▌" if selected else " ", style=ACCENT)
    head.append(f"#{flight.card_id} ", style=f"bold {ACCENT}" if selected else "bold")
    head.append(f"{flight.project}  ", style=MUTED)
    head.append(flight.title, style=TEXT)
    if flight.queued:
        if flight.hold:
            head.append(f"   held: {flight.hold}", style=WARN)
        else:
            head.append("   waiting for a slot", style=FAINT)
        if flight.status == "rework":
            head.append(" (rework)", style=WARN)
        return head
    elapsed = (now - flight.since).total_seconds() if flight.since else None
    live = flight.live
    lead = live.get("lead") if isinstance(live.get("lead"), dict) else {}
    if lead:
        head.append(f"   {lead.get('name')}", style=f"bold {lead.get('color') or MUTED}")
    model = str(live.get("model") or "").replace("claude-", "")
    meta = (f" · {model}" if model else "") + f" · try {flight.attempt} · {clock(elapsed)}"
    head.append(meta, style=FAINT)
    if flight.denials:
        head.append(f" · ⛨ {flight.denials}", style=BAD)
    if header_only:
        return head
    rail = Text("   ")
    rail.append_text(route_text(flight.route))
    lines: list[RenderableType] = [head, rail]
    gauges = Text("   ")
    tasks = flight.tasks
    if tasks:
        done = sum(1 for t in tasks if t.get("status") == "completed")
        gauges.append("tasks ", style=FAINT)
        gauges.append_text(screens.bar(done / len(tasks), 10, OK))
        gauges.append(f" {done}/{len(tasks)}  ", style=TEXT)
        current = next((t for t in tasks if t.get("status") == "in_progress"), None)
        if current:
            gauges.append(f"now: {current.get('subject')}  ", style=MUTED)
    share = flight.context_share
    if share is not None:
        gauges.append("memory ", style=FAINT)
        gauges.append_text(screens.bar(share, 8, WARN if share > 0.8 else ACCENT))
        gauges.append(f" {share:.0%}  ", style=TEXT)
        if live.get("compactions"):
            gauges.append(f"compacted {live['compactions']}x  ", style=WARN)
    running = [h for h in live.get("helpers") or [] if h.get("state") == "running"]
    if running:
        gauges.append(
            "helpers: " + ", ".join(str(h.get("name")) for h in running) + "  ", style=ACCENT
        )
    progress = live.get("progress")
    if isinstance(progress, dict) and progress.get("total"):
        gauges.append(f"{progress.get('phase') or 'progress'} ", style=FAINT)
        gauges.append_text(screens.bar(float(progress["done"]) / float(progress["total"]), 8))
        gauges.append(f" {progress['done']:.0f}/{progress['total']:.0f}  ", style=TEXT)
    if gauges.plain.strip():
        gauges.no_wrap = True
        gauges.overflow = "ellipsis"
        lines.append(gauges)
    for item in flight.activity[-4:]:
        mark, colour = CATEGORY_MARK.get(item.category, ("·", MUTED))
        line = Text(f"   {mark} ", style=colour)
        line.append(item.title[:160], style=colour if item.category != "tool" else TEXT)
        line.overflow = "ellipsis"
        line.no_wrap = True
        lines.append(line)
    lines.append(Text(""))
    return Group(*lines)


def waiting_block(item: Waiting, *, selected: bool) -> RenderableType:
    colour, mark, label = WAITING_MARK.get(item.status, (MUTED, "·", item.status))
    head = Text()
    head.append("▌" if selected else " ", style=ACCENT)
    head.append(f"{mark} ", style=f"bold {colour}")
    head.append(f"#{item.card_id} ", style=f"bold {ACCENT}" if selected else "bold")
    head.append(f"{label}", style=colour)
    if item.verdict:
        head.append(f"  {item.verdict}", style=VERDICT_STYLE.get(item.verdict, MUTED))
    if item.gates_total:
        passed = item.gates_passed == item.gates_total
        head.append(f"  gates {item.gates_passed}/{item.gates_total}", style=OK if passed else BAD)
    if item.tables_changed:
        head.append(f"  ⛁ {item.tables_changed}", style=MUTED)
    if item.lesson:
        head.append("  lesson", style=MUTED)
    title = Text(f"   {item.title}", style=TEXT, no_wrap=True, overflow="ellipsis")
    title.append(f"  {item.project}", style=FAINT)
    detail = Text(f"   {item.detail[:200]}", style=FAINT, no_wrap=True, overflow="ellipsis")
    return Group(head, title, detail, Text(""))


class Panel(Static):
    """A bordered panel whose title sits in its border."""

    def __init__(self, title: str, *, id: str) -> None:
        super().__init__(id=id)
        self.border_title = title


class Confirm(ModalScreen[bool]):
    """Yes or no, before anything that changes something."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "answer(True)", "yes"),
        Binding("n,escape", "answer(False)", "no"),
    ]

    def __init__(self, question: str, yes: str = "Approve (y)") -> None:
        super().__init__()
        self.question = question
        self.yes = yes

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Static(self.question, id="question")
            with Horizontal(id="answers"):
                yield Button(self.yes, id="yes", variant="warning")
                yield Button("Cancel (n)", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)


class MissionControl(App[None]):
    TITLE = "PRAT mission control"
    CSS = f"""
    Screen {{ background: #0a1022; color: {TEXT}; }}
    #top {{ height: 1; padding: 0 1; background: #101930; }}
    #grid {{
        grid-size: 2 2;
        grid-columns: 3fr 2fr;
        grid-rows: 3fr 2fr;
        grid-gutter: 0 1;
        padding: 0 1;
    }}
    Panel {{
        border: round #24314f;
        border-title-color: {ACCENT};
        border-title-style: bold;
        padding: 0 1;
        overflow-y: auto;
        height: 100%;
    }}
    Panel:focus-within {{ border: round #3a4868; }}
    #pulse {{ height: 3; margin: 0 1; border: round #24314f; border-title-color: {ACCENT}; }}
    #focus {{ display: none; }}
    Screen.following #grid {{ display: none; }}
    Screen.following #focus {{ display: block; height: 1fr; margin: 0 1; }}
    Confirm {{ align: center middle; background: rgba(10, 16, 34, 0.7); }}
    #confirm {{
        width: 64; height: auto; padding: 1 2;
        border: round {ACCENT}; background: #101930;
    }}
    #answers {{ height: auto; margin-top: 1; }}
    #answers Button {{ margin-right: 2; }}
    #alerts {{ height: auto; max-height: 3; padding: 0 1; display: none; }}
    Screen.alerting #alerts {{ display: block; }}
    .view {{ display: none; height: 1fr; margin: 0 1; }}
    {VIEW_CSS}
    Ask, Pick, Reply {{ align: center middle; background: rgba(10, 16, 34, 0.7); }}
    #ask, #pick, #reply {{
        width: 76; height: auto; max-height: 24; padding: 1 2;
        border: round {ACCENT}; background: #101930;
    }}
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("1", "view('deck')", "deck"),
        Binding("2", "view('crew')", "crew"),
        Binding("3", "view('jobs')", "jobs"),
        Binding("4", "view('epics')", "epics"),
        Binding("5", "view('machine')", "machine"),
        Binding("6", "view('skills')", "skills"),
        Binding("7", "view('usage')", "usage"),
        Binding("j,down", "move(1)", "next", show=False),
        Binding("k,up", "move(-1)", "previous", show=False),
        Binding("enter", "follow", "follow", show=False),
        Binding("escape", "unfollow", "back", show=False),
        Binding("a", "approve", "approve"),
        Binding("r", "letter('r')", "rework / answer"),
        Binding("s", "letter('s')", "start job", show=False),
        Binding("x", "letter('x')", "stop job", show=False),
        Binding("l", "letter('l')", "land", show=False),
        Binding("d", "letter('d')", "discard", show=False),
        Binding("p", "letter('p')", "promote", show=False),
        Binding("e", "restart_environment", "restart env", show=False),
        Binding("t", "letter('t')", "talk", show=False),
        Binding("u", "letter('u')", "stand-up", show=False),
        Binding("i", "letter('i')", "introduce", show=False),
        Binding("o", "open", "open in web", show=False),
        Binding("c", "client", "client", show=False),
        Binding("f5", "refresh", "refresh", show=False),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self, settings: Settings, db: Database, client: str | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.db = db
        self.data = MissionData(settings, db)
        self.client = client if client is not None else settings.default_client
        self.snapshot: Snapshot | None = None
        self.selected: int | None = None
        self.following: int | None = None
        self._loading = False
        self.view = "deck"
        # Selection within the jobs and skills screens.
        self.cursor: dict[str, int] = {"jobs": 0, "skills": 0, "crew": 0}
        self.remotes: list[RemoteDeck] = []
        self.machine: Any = None
        self.groups: list[tuple[str, list[Any]]] = []
        self.verdict = ""
        self.skill_rows: list[tuple[str, list[tuple[str, str, str, int, float | None, str]]]] = []
        self.skill_candidates: list[tuple[str, str, str]] = []
        self._slow_at = 0.0

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        yield Static(id="alerts")
        with Grid(id="grid"):
            yield Panel("in flight", id="flights")
            yield Panel("needs you", id="waiting")
            yield Panel("fleet", id="fleet")
            yield Panel("guardrails · refused in the last day", id="guards")
        yield Panel("following", id="focus")
        for view in VIEWS:
            panel = Panel(VIEW_TITLES[view], id=f"view-{view}")
            panel.add_class("view")
            yield panel
        yield Panel("pulse", id="pulse")
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh()
        self.set_interval(REFRESH_SECONDS, self.action_refresh)

    # Loading ---------------------------------------------------------------------------

    def action_refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.run_worker(self._load, thread=True, exclusive=True, group="load")

    def _load(self) -> None:
        try:
            snapshot = self.data.snapshot(self.client)
        except Exception as exc:  # keep drawing; say what went wrong
            self.call_from_thread(self._failed, exc)
            return
        try:
            self._load_slow(snapshot)
        except Exception as exc:
            self.call_from_thread(
                self.notify, f"could not read everything: {exc}", severity="warning"
            )
        self.call_from_thread(self._show, snapshot)

    def _load_slow(self, snapshot: Snapshot) -> None:
        """The machine, other installations, skills: read less often than the deck."""
        self.machine = governor.machine(
            processes=True, root=governor.server_pid(self.settings.home)
        )
        self.groups = group_processes(self.machine.processes, self.settings)
        self.verdict = governor.Governor(self.settings, self.db).headroom()
        now = time.monotonic()
        if now - self._slow_at < SLOW_SECONDS:
            return
        self._slow_at = now
        self.remotes = [fetch_remote(remote) for remote in self.settings.remotes]
        rows = []
        candidates: list[tuple[str, str, str]] = []
        for line in snapshot.projects:
            cards = skills.scorecards(self.db, self.settings.home, line.slug)
            used = sorted(
                (c for c in cards.values() if c.loads or c.tier == "project"),
                key=lambda c: c.loads,
                reverse=True,
            )
            if used:
                rows.append(
                    (
                        line.slug,
                        [
                            (
                                c.name,
                                c.tier,
                                crew.skill_phase(self.settings.home, line.slug, c.name),
                                c.loads,
                                c.first_review_rate,
                                c.advice,
                            )
                            for c in used
                        ],
                    )
                )
            candidates += [
                (line.slug, c.name, c.description)
                for c in skills.candidates(self.settings.home, line.slug)
            ]
        self.skill_rows, self.skill_candidates = rows, candidates

    def _failed(self, exc: Exception) -> None:
        self._loading = False
        self.query_one("#top", Static).update(Text(f" PRAT  could not read: {exc}", style=BAD))

    def _show(self, snapshot: Snapshot) -> None:
        self._loading = False
        self.snapshot = snapshot
        order = self._order()
        if self.selected not in order:
            self.selected = order[0] if order else None
        self._draw()

    def _order(self) -> list[int]:
        if self.snapshot is None:
            return []
        return [f.card_id for f in self.snapshot.flights] + [
            w.card_id for w in self.snapshot.waiting
        ]

    # Drawing ---------------------------------------------------------------------------

    def _draw(self) -> None:
        snap = self.snapshot
        if snap is None:
            return
        now = datetime.now(UTC)
        self.query_one("#top", Static).update(self._top(snap, now))
        alerts = list(snap.alerts) + [
            f"{deck.name}: {alert}" for deck in self.remotes for alert in deck.alerts
        ]
        self.screen.set_class(bool(alerts), "alerting")
        if alerts:
            text = Text()
            for alert in alerts[:3]:
                text.append("▲ ", style=f"bold {WARN}")
                shown = alert if len(alert) <= 150 else alert[:150] + "..."
                text.append(shown + "\n", style=WARN)
            if len(alerts) > 3:
                text.append(f"  and {len(alerts) - 3} more", style=FAINT)
            self.query_one("#alerts", Static).update(text)
        self.query_one("#pulse", Panel).update(self._pulse(snap))
        if self.view == "deck":
            self.query_one("#flights", Panel).update(self._flights(snap, now))
            self.query_one("#waiting", Panel).update(self._waiting(snap))
            self.query_one("#fleet", Panel).update(self._fleet(snap))
            self.query_one("#guards", Panel).update(self._guards(snap))
            if self.following is not None:
                self.query_one("#focus", Panel).update(self._focus(snap, now))
            return
        panel = self.query_one(f"#view-{self.view}", Panel)
        if self.view == "crew":
            panel.update(screens.crew_screen(snap, self.cursor["crew"]))
        elif self.view == "jobs":
            panel.update(screens.jobs_screen(snap, self.cursor["jobs"]))
        elif self.view == "epics":
            panel.update(screens.epics_screen(snap))
        elif self.view == "machine" and self.machine is not None:
            panel.update(screens.machine_screen(snap, self.machine, self.groups, self.verdict))
        elif self.view == "skills":
            panel.update(
                screens.skills_screen(self.skill_rows, self.skill_candidates, self.cursor["skills"])
            )
        elif self.view == "usage":
            panel.update(screens.usage_screen(snap))

    def _top(self, snap: Snapshot, now: datetime) -> Text:
        text = Text()
        text.append("◢ PRAT ", style=f"bold {ACCENT}")
        text.append("mission control", style=MUTED)
        text.append("   ")
        for name in [None, *snap.clients]:
            chosen = name == snap.client
            label = name or "all clients"
            text.append(f" {label} ", style=f"bold #1b1405 on {ACCENT}" if chosen else FAINT)
        text.append("   ")
        text.append(f"▶ {snap.running} in flight", style=ACCENT if snap.running else FAINT)
        text.append(f" · {snap.queued} queued", style=FAINT)
        need = len(snap.waiting)
        text.append(f" · ◆ {need} need you", style=WARN if need else FAINT)
        refused = len(snap.refusals)
        text.append(f" · ⛨ {refused} refused", style=BAD if refused else FAINT)
        text.append(f"   {now.astimezone().strftime('%H:%M:%S')}", style=FAINT)
        return text

    def _flights(self, snap: Snapshot, now: datetime) -> RenderableType:
        parts: list[RenderableType] = [
            flight_block(f, now, selected=f.card_id == self.selected) for f in snap.flights
        ]
        if not parts:
            parts.append(
                Text("No agent is working. Cards you move to ready start here.\n", style=FAINT)
            )
        if snap.landed:
            parts.append(Text("landed", style=f"bold {MUTED}"))
            for item in snap.landed:
                line = Text(no_wrap=True, overflow="ellipsis")
                line.append("  ✓ ", style=OK)
                line.append(item.at.astimezone().strftime("%a %H:%M  "), style=FAINT)
                line.append(f"#{item.card_id} ", style="bold")
                line.append(f"{item.project}  ", style=MUTED)
                line.append(item.title, style=TEXT)
                line.append(f"  {item.commit}", style=FAINT)
                if item.databases:
                    line.append("  ⛁ with data", style=MUTED)
                parts.append(line)
        for deck in self.remotes:
            parts.append(Text(f"\non {deck.name}", style=f"bold {MUTED}"))
            if deck.error:
                parts.append(Text(f"  not reachable: {deck.error}", style=WARN))
            for flight in deck.flights:
                parts.append(flight_block(flight, now, selected=False))
            if not deck.error and not deck.flights:
                parts.append(Text("  nothing in flight", style=FAINT))
        return Group(*parts)

    def _waiting(self, snap: Snapshot) -> RenderableType:
        parts: list[RenderableType] = [
            waiting_block(w, selected=w.card_id == self.selected) for w in snap.waiting
        ]
        if not parts:
            parts.append(Text("Nothing is waiting on you.", style=FAINT))
        for deck in self.remotes:
            if deck.waiting:
                parts.append(Text(f"on {deck.name}", style=f"bold {MUTED}"))
                parts.extend(waiting_block(w, selected=False) for w in deck.waiting)
        return Group(*parts)

    def _fleet(self, snap: Snapshot) -> RenderableType:
        table = Table.grid(padding=(0, 2), expand=True)
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True)
        table.add_column(ratio=1)
        table.add_column(justify="right", no_wrap=True)
        for line in snap.projects:
            name = Text(line.slug, style=TEXT)
            if line.client and snap.client is None:
                name.append(f" {line.client}", style=FAINT)
            data = Text(" ".join(f"⛁ {d}" for d in line.databases) or "·", style=MUTED)
            envs = Text()
            if line.problem:
                envs.append(line.problem[:60], style=WARN)
            for env in line.environments:
                colour = ENV_STYLE.get(env.state, FAINT)
                envs.append("● " if env.state == "running" else "○ ", style=colour)
                envs.append(env.name, style=TEXT if env.state == "running" else FAINT)
                if env.port and env.state != "stopped":
                    envs.append(f":{env.port}", style=FAINT)
                envs.append("  ")
            cards = Text(f"{line.open_cards} open" if line.open_cards else "", style=MUTED)
            table.add_row(name, data, envs, cards)
        if not snap.projects:
            return Text("No projects yet: prat project add <link>", style=FAINT)
        return table

    def _guards(self, snap: Snapshot) -> RenderableType:
        if not snap.refusals:
            return Text("The policy refused nothing in the last day.", style=FAINT)
        lines = []
        for item in snap.refusals:
            line = Text(no_wrap=True, overflow="ellipsis")
            line.append(item.at.astimezone().strftime("%H:%M "), style=FAINT)
            line.append(f"#{item.card_id} ", style="bold")
            line.append(f"{item.project} ", style=MUTED)
            line.append(item.title[:200], style=BAD)
            lines.append(line)
        return Group(*lines)

    def _pulse(self, snap: Snapshot) -> Text:
        pulse = snap.pulse
        text = Text()
        busy = sum(pulse.busy_minutes)
        text.append("agents busy 24h ", style=FAINT)
        text.append(sparkline(pulse.busy_minutes), style=ACCENT)
        text.append(f"  {clock(busy * 60)} agent time", style=TEXT)
        text.append(f"  ·  {pulse.runs_today} runs today", style=MUTED)
        text.append(f"  ·  {pulse.landed_today} landed", style=OK if pulse.landed_today else MUTED)
        text.append(f"  ·  {pulse.lessons_today} lessons", style=MUTED)
        if pulse.dream:
            text.append(f"  ·  ☾ {pulse.dream}", style=MUTED)
        return text

    def _focus(self, snap: Snapshot, now: datetime) -> RenderableType:
        item = snap.card(self.following) if self.following is not None else None
        if item is None:
            return Text(
                f"Card #{self.following} has left the board (approved, cancelled or done). "
                "esc goes back.",
                style=FAINT,
            )
        if isinstance(item, Waiting):
            return Group(
                waiting_block(item, selected=True),
                Text(item.detail, style=TEXT),
                Text(
                    "\na approves (after a confirmation) · o opens it in the web UI · esc back",
                    style=FAINT,
                ),
            )
        head = flight_block(item, now, selected=True, header_only=True)
        rail = Text("\n   ")
        rail.append_text(route_text(item.route, timings=True))
        log = []
        for entry in item.activity:
            mark, colour = CATEGORY_MARK.get(entry.category, ("·", MUTED))
            line = Text(f"   {clock(entry.offset):>7} {mark} ", style=FAINT)
            line.append(entry.title, style=colour if entry.category != "tool" else TEXT)
            log.append(line)
        return Group(head, rail, Text(""), *log)

    # Actions ---------------------------------------------------------------------------

    def action_view(self, view: str) -> None:
        for name in VIEWS:
            self.screen.remove_class(f"view-{name}")
        if view != "deck":
            self.screen.add_class(f"view-{view}")
            self.action_unfollow()
        self.view = view
        self._draw()

    def action_move(self, step: int) -> None:
        if self.view in self.cursor:
            snap = self.snapshot
            count = {
                "jobs": len(snap.jobs) if snap else 0,
                "crew": len(snap.crew) if snap else 0,
                "skills": len(self.skill_candidates),
            }[self.view]
            if count:
                self.cursor[self.view] = (self.cursor[self.view] + step) % count
            self._draw()
            return
        order = self._order()
        if not order:
            return
        index = order.index(self.selected) if self.selected in order else 0
        self.selected = order[(index + step) % len(order)]
        if self.following is not None:
            self.following = self.selected
        self._draw()

    def action_follow(self) -> None:
        if self.selected is None:
            return
        self.following = self.selected
        self.query_one("#focus", Panel).border_title = f"following #{self.following}"
        self.screen.add_class("following")
        self._draw()

    def action_unfollow(self) -> None:
        self.following = None
        self.screen.remove_class("following")
        self._draw()

    def action_client(self) -> None:
        if self.snapshot is None:
            return
        choices: list[str | None] = [None, *self.snapshot.clients]
        index = choices.index(self.client) if self.client in choices else 0
        self.client = choices[(index + 1) % len(choices)]
        self.action_refresh()

    def action_open(self) -> None:
        if self.selected is None:
            return
        host = self.settings.web_host
        shown = f"[{host}]" if ":" in host else host
        webbrowser.open(f"http://{shown}:{self.settings.web_port}/cards/{self.selected}")

    def action_approve(self) -> None:
        snap = self.snapshot
        item = snap.card(self.selected) if snap and self.selected is not None else None
        if not isinstance(item, Waiting) or item.status != "review":
            self.notify("Only a card in review can be approved.", severity="warning")
            return
        verdict = f" The reviewer said {item.verdict}." if item.verdict else ""
        question = (
            f"Approve #{item.card_id} {item.title}?{verdict}\n\n"
            "Its data, then its code, merge into the default branch, locally."
        )

        def answered(yes: bool | None) -> None:
            if yes:
                self.run_worker(self._approve(item.card_id), exclusive=False, group="approve")

        self.push_screen(Confirm(question), answered)

    async def _approve(self, card_id: int) -> None:
        """Through the server when it runs (its engine starts the parts of an approved epic
        and sets up the checkout), else here, as ``prat card approve`` does."""
        sent = await asyncio.to_thread(self._post, "approve", {"card_id": card_id})
        if sent is not None:
            self._report(sent)
            return
        from prat.engine import ApproveError, Engine
        from prat.git import GitError
        from prat.runtime import FakeRuntime

        engine = Engine(self.settings, self.db, FakeRuntime([]))
        try:
            merged = await engine.approve(card_id)
        except (ApproveError, GitError) as exc:
            self.notify(f"#{card_id} not merged: {exc}", severity="error", timeout=10)
            return
        self.notify(f"#{card_id} merged as {merged.commit[:10]}", timeout=6)
        self.action_refresh()
        await asyncio.shield(engine.checkouts_set_up())

    # Acting through the server ------------------------------------------------------------

    def _post(self, action: str, body: dict[str, Any]) -> tuple[bool, str] | None:
        """POST an action to this installation's server; None when it is not running."""
        import httpx

        from prat.web.tokens import read_access_token

        token = read_access_token(self.settings)
        if not token:
            return None
        host = self.settings.web_host
        shown = f"[{host}]" if ":" in host else host
        url = f"http://{shown}:{self.settings.web_port}/api/act/{action}"
        try:
            response = httpx.post(
                url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=60
            )
        except httpx.HTTPError:
            return None
        try:
            data = response.json()
        except ValueError:
            return (False, f"the server answered {response.status_code}")
        if response.status_code == 200:
            return (True, str(data.get("message") or "done"))
        return (False, str(data.get("error") or response.status_code))

    def _report(self, sent: tuple[bool, str] | None) -> None:
        if sent is None:
            self.notify(
                "PRAT's server is not running here: start it with prat serve.", severity="error"
            )
            return
        ok, message = sent
        self.notify(message, severity="information" if ok else "error", timeout=8)
        self.action_refresh()

    def _act(self, action: str, body: dict[str, Any]) -> None:
        async def go() -> None:
            self._report(await asyncio.to_thread(self._post, action, body))

        self.run_worker(go(), exclusive=False, group="act")

    def action_letter(self, letter: str) -> None:
        if self.view == "jobs":
            self._job_key(letter)
        elif self.view == "skills":
            self._skill_key(letter)
        elif self.view == "crew":
            self._crew_key(letter)
        elif letter == "r":
            self._rework()

    def _crew_key(self, letter: str) -> None:
        snap = self.snapshot
        if letter == "u":
            self.notify("Stand-up: everyone who worked today is giving their report...")
            self._converse("standup", {}, "Stand-up")
            return
        if snap is None or not snap.crew:
            return
        member = snap.crew[min(self.cursor["crew"], len(snap.crew) - 1)]
        name = member.name or member.agent
        if letter == "t":

            def said(text: str | None) -> None:
                if text:
                    self._converse("talk", {"agent": member.agent, "message": text}, name)

            self.push_screen(Ask(f"Say something to {name}:"), said)
        elif letter == "i":
            self._converse("introduce", {"agent": member.agent}, f"{name} introduces themselves")

    def _converse(self, action: str, body: dict[str, Any], title: str) -> None:
        async def go() -> None:
            sent = await asyncio.to_thread(self._post, action, body)
            if sent is None:
                self._report(None)
                return
            ok, message = sent
            self.push_screen(Reply(title, message if ok else f"Could not: {message}"))
            self._slow_at = 0.0
            self.data._crew_cache = None
            self.action_refresh()

        self.run_worker(go(), exclusive=False, group="converse")

    def _rework(self) -> None:
        snap = self.snapshot
        item = snap.card(self.selected) if snap and self.selected is not None else None
        if not isinstance(item, Waiting):
            self.notify("Choose a card that needs you (j/k).", severity="warning")
            return
        if item.status == "blocked":
            question = f"#{item.card_id} asks:\n{item.detail}\n\nYour answer:"
        else:
            question = f"Send #{item.card_id} {item.title} back. What should change?"

        def answered(text: str | None) -> None:
            if text:
                self._act("rework", {"card_id": item.card_id, "feedback": text})

        self.push_screen(Ask(question), answered)

    def _job_key(self, letter: str) -> None:
        snap = self.snapshot
        if snap is None:
            return
        if letter == "s":
            choices = [
                (f"{slug}: {name}", (slug, name))
                for slug, names in snap.declared_jobs.items()
                for name in names
            ]
            if not choices:
                self.notify("No project declares a [[job]].", severity="warning")
                return

            def chosen(value: Any) -> None:
                if value:
                    self._act("job-start", {"project": value[0], "name": value[1]})

            self.push_screen(Pick("Start which job?", choices), chosen)
            return
        if not snap.jobs:
            return
        job = snap.jobs[min(self.cursor["jobs"], len(snap.jobs) - 1)]
        action = {"x": "job-stop", "l": "job-land", "d": "job-discard"}.get(letter)
        if action is None:
            return
        verb = {
            "job-stop": "Stop",
            "job-land": "Land the data of",
            "job-discard": "Discard the data of",
        }[action]

        def confirmed(yes: bool | None) -> None:
            if yes:
                self._act(action, {"run_id": job.run_id})

        self.push_screen(
            Confirm(f"{verb} job run {job.run_id} ({job.title})?", "Yes (y)"), confirmed
        )

    def _skill_key(self, letter: str) -> None:
        if letter not in ("p", "r") or not self.skill_candidates:
            return
        slug, name, _description = self.skill_candidates[
            min(self.cursor["skills"], len(self.skill_candidates) - 1)
        ]
        try:
            if letter == "p":
                skills.promote(self.settings.home, slug, name)
                self.notify(f"{name} promoted: {slug}'s agents carry it from their next run")
            else:
                skills.reject(self.settings.home, slug, name)
                self.notify(f"{name} rejected")
        except skills.SkillError as exc:
            self.notify(str(exc), severity="error")
        self._slow_at = 0.0
        self.action_refresh()

    def action_restart_environment(self) -> None:
        snap = self.snapshot
        if snap is None:
            return
        choices = [
            (f"{line.slug}: {env.name} ({env.state})", (line.slug, env.name))
            for line in snap.projects
            for env in line.environments
        ]
        if not choices:
            return

        def chosen(value: Any) -> None:
            if value:
                self._act("env-restart", {"project": value[0], "name": value[1]})

        self.push_screen(Pick("Restart which environment?", choices), chosen)


class Reply(ModalScreen[None]):
    """What an agent said back, or the crew's stand-up."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape,enter,q", "close", "close")]

    def __init__(self, title: str, text: str) -> None:
        super().__init__()
        self.title_text = title
        self.text = text

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="reply"):
            yield Static(Text(self.title_text, style=f"bold {ACCENT}"))
            yield Static(Text(self.text, style=TEXT))
            yield Static(Text("esc closes", style=FAINT))

    def action_close(self) -> None:
        self.dismiss(None)


class Ask(ModalScreen[str | None]):
    """A question with a line of text for the answer."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="ask"):
            yield Static(self.question)
            yield Input(placeholder="type and press enter; esc cancels", id="answer")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class Pick(ModalScreen[Any]):
    """Choose one of a few things."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel")]

    def __init__(self, question: str, choices: list[tuple[str, Any]]) -> None:
        super().__init__()
        self.question = question
        self.choices = choices

    def compose(self) -> ComposeResult:
        with Vertical(id="pick"):
            yield Static(self.question)
            yield OptionList(*(label for label, _value in self.choices), id="options")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.choices[event.option_index][1])

    def action_cancel(self) -> None:
        self.dismiss(None)


def group_processes(processes: list[Any], settings: Settings) -> list[tuple[str, list[Any]]]:
    """PRAT's processes grouped by what they are for: each card's worktree, the agents
    themselves, jobs, the person's checkouts."""
    worktrees = str(settings.worktrees_dir).lower()
    jobs_root = str(settings.home / "jobs").lower()
    groups: dict[str, list[Any]] = {}
    for process in processes:
        cwd = (process.cwd or "").lower().replace("\\", "/")
        name = process.name.lower()
        if cwd.startswith(worktrees.replace("\\", "/")):
            folder = cwd[len(worktrees) :].strip("/").split("/")[0]
            label = f"card worktree {folder}"
        elif cwd.startswith(jobs_root.replace("\\", "/")) or "jobrunner" in name:
            label = "jobs"
        elif "claude" in name:
            label = "agents (Claude Code)"
        elif cwd:
            label = f"in {cwd.rstrip('/').split('/')[-1]}"
        else:
            label = name
        groups.setdefault(label, []).append(process)
    return sorted(groups.items(), key=lambda kv: -sum(p.memory_mb for p in kv[1]))


def run(settings: Settings, db: Database, client: str | None = None) -> None:
    MissionControl(settings, db, client).run()
