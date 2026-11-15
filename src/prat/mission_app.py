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
import webbrowser
from datetime import UTC, datetime
from typing import ClassVar

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static

from prat.config import Settings
from prat.db import Database
from prat.mission import (
    Flight,
    Leg,
    MissionData,
    Snapshot,
    Waiting,
    sparkline,
)

REFRESH_SECONDS = 2.0

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
        head.append("   waiting for a slot", style=FAINT)
        if flight.status == "rework":
            head.append(" (rework)", style=WARN)
        return head
    elapsed = (now - flight.since).total_seconds() if flight.since else None
    meta = f"   try {flight.attempt} · {clock(elapsed)}"
    head.append(meta, style=FAINT)
    if flight.denials:
        head.append(f" · ⛨ {flight.denials}", style=BAD)
    if header_only:
        return head
    rail = Text("   ")
    rail.append_text(route_text(flight.route))
    lines: list[RenderableType] = [head, rail]
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

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Static(self.question, id="question")
            with Horizontal(id="answers"):
                yield Button("Approve (y)", id="yes", variant="warning")
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
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("j,down", "move(1)", "next", show=False),
        Binding("k,up", "move(-1)", "previous", show=False),
        Binding("enter", "follow", "follow"),
        Binding("escape", "unfollow", "back", show=False),
        Binding("a", "approve", "approve"),
        Binding("o", "open", "open in web"),
        Binding("c", "client", "client"),
        Binding("r", "refresh", "refresh", show=False),
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

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        with Grid(id="grid"):
            yield Panel("in flight", id="flights")
            yield Panel("needs you", id="waiting")
            yield Panel("fleet", id="fleet")
            yield Panel("guardrails · refused in the last day", id="guards")
        yield Panel("following", id="focus")
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
        self.call_from_thread(self._show, snapshot)

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
        self.query_one("#flights", Panel).update(self._flights(snap, now))
        self.query_one("#waiting", Panel).update(self._waiting(snap))
        self.query_one("#fleet", Panel).update(self._fleet(snap))
        self.query_one("#guards", Panel).update(self._guards(snap))
        self.query_one("#pulse", Panel).update(self._pulse(snap))
        if self.following is not None:
            self.query_one("#focus", Panel).update(self._focus(snap, now))

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
        return Group(*parts)

    def _waiting(self, snap: Snapshot) -> RenderableType:
        if not snap.waiting:
            return Text("Nothing is waiting on you.", style=FAINT)
        return Group(*(waiting_block(w, selected=w.card_id == self.selected) for w in snap.waiting))

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

    def action_move(self, step: int) -> None:
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


def run(settings: Settings, db: Database, client: str | None = None) -> None:
    MissionControl(settings, db, client).run()
