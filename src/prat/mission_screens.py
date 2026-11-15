"""The screens of ``prat mission`` beyond the deck: crew, jobs, epics, machine, skills, usage.

Each is a pure function from what mission control read to a Rich renderable, so the app only
decides which one is on screen. Colours follow PRAT's dark theme (prat.mission_app).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from prat.mission import Flight, Snapshot

ACCENT = "#f6b73c"
OK = "#4ade80"
WARN = "#fb923c"
BAD = "#f87171"
MUTED = "#9aa6bc"
FAINT = "#5d6a85"
TEXT = "#eef2f8"


def bar(fraction: float | None, width: int = 20, colour: str = ACCENT) -> Text:
    """A bar of eighth blocks: ``fraction`` of ``width`` filled."""
    text = Text()
    if fraction is None:
        text.append("░" * width, style=FAINT)
        return text
    fraction = max(0.0, min(1.0, fraction))
    eighths = round(fraction * width * 8)
    full, part = divmod(eighths, 8)
    text.append("█" * full, style=colour)
    if part and full < width:
        text.append(" ▏▎▍▌▋▊▉"[part], style=colour)
        full += 1
    text.append("░" * (width - full), style=FAINT)
    return text


def clock(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def mood_word(mood: float) -> tuple[str, str]:
    if mood >= 0.4:
        return "upbeat", OK
    if mood >= 0.1:
        return "steady", TEXT
    if mood > -0.2:
        return "even", MUTED
    if mood > -0.5:
        return "uneasy", WARN
    return "low", BAD


# Crew -------------------------------------------------------------------------------------


def crew_screen(snap: Snapshot, selected: int = 0) -> RenderableType:
    if not snap.crew:
        return Text(
            "No agent has a memory yet. Each lead, helper and reviewer starts remembering its "
            "work the first time it runs.",
            style=FAINT,
        )
    table = Table.grid(padding=(0, 2), expand=True)
    for _ in range(6):
        table.add_column(no_wrap=True)
    table.add_column(ratio=1)
    head = [
        Text(h, style=f"bold {MUTED}")
        for h in ("agent", "role", "now", "landed", "sent back", "mood")
    ]
    table.add_row(*head, Text("remembers", style=f"bold {MUTED}"))
    for index, member in enumerate(snap.crew):
        chosen = index == selected
        name = Text("▌" if chosen else " ", style=ACCENT)
        name.append("● ", style=member.color or MUTED)
        name.append(member.name or member.agent, style=f"bold {ACCENT if chosen else TEXT}")
        now = Text(
            " ".join(f"#{c}" for c in member.working_on) or "·",
            style=ACCENT if member.working_on else FAINT,
        )
        word, colour = mood_word(member.mood)
        mood = Text(f"{word} {member.mood:+.1f}", style=colour)
        memory = Text(f"{member.episodes} episodes, {member.beliefs} beliefs", style=MUTED)
        if member.last:
            memory.append(f"  last: {member.last}", style=FAINT)
        table.add_row(
            name,
            Text(member.who, style=MUTED),
            now,
            Text(str(member.landed), style=OK if member.landed else FAINT),
            Text(str(member.reworked), style=WARN if member.reworked else FAINT),
            mood,
            memory,
        )
    parts: list[RenderableType] = [table, Text("")]
    if snap.crew:
        member = snap.crew[min(selected, len(snap.crew) - 1)]
        card = Text()
        card.append(f"{member.name or member.agent}", style=f"bold {ACCENT}")
        card.append(f"  {member.who}", style=MUTED)
        if member.personality:
            card.append(f"  ·  {member.personality}", style=FAINT)
        parts.append(card)
        parts.append(
            Text(
                member.bio or "Has not introduced itself yet (i asks it to).",
                style=TEXT if member.bio else FAINT,
            )
        )
        parts.append(Text(""))
    for flight in snap.flights:
        if flight.queued or not flight.live:
            continue
        parts.append(crew_at_work(flight))
    parts.append(
        Text(
            "j/k choose · t talk with them · u stand-up with the whole crew · i ask them to "
            "introduce themselves",
            style=FAINT,
        )
    )
    return Group(*parts)


def crew_at_work(flight: Flight) -> RenderableType:
    head = Text()
    head.append(f"#{flight.card_id} ", style=f"bold {ACCENT}")
    head.append(f"{flight.lead or 'lead'} ", style=f"bold {TEXT}")
    head.append(f"on {flight.title}", style=MUTED)
    lines: list[RenderableType] = [head]
    for helper in flight.live.get("helpers") or []:
        state = str(helper.get("state") or "")
        colour = ACCENT if state == "running" else (OK if state == "done" else BAD)
        mark = "◉" if state == "running" else ("✓" if state == "done" else "✕")
        line = Text(f"   {mark} {helper.get('name')}: ", style=colour)
        said = helper.get("summary") if state != "running" else helper.get("prompt")
        line.append(str(said or "")[:150], style=TEXT if state == "running" else MUTED)
        line.no_wrap = True
        line.overflow = "ellipsis"
        lines.append(line)
    skills = flight.live.get("skills") or []
    if skills:
        lines.append(Text("   skills loaded: " + ", ".join(skills), style=MUTED))
    thought = flight.live.get("thought")
    if thought:
        line = Text(f"   thinking: {thought}", style=FAINT, no_wrap=True, overflow="ellipsis")
        lines.append(line)
    lines.append(Text(""))
    return Group(*lines)


# Jobs -------------------------------------------------------------------------------------


def jobs_screen(snap: Snapshot, selected: int) -> RenderableType:
    parts: list[RenderableType] = []
    if not snap.jobs:
        parts.append(Text("No job has run yet.", style=FAINT))
    for index, job in enumerate(snap.jobs):
        chosen = index == selected
        head = Text("▌" if chosen else " ", style=ACCENT)
        head.append(f"run {job.run_id} ", style=f"bold {ACCENT}" if chosen else "bold")
        head.append(f"{job.slug}  ", style=MUTED)
        head.append(job.title, style=TEXT)
        status = "STALLED" if job.stalled else job.status
        colour = {"running": ACCENT, "succeeded": OK, "failed": BAD, "stopped": WARN}.get(
            job.status, MUTED
        )
        head.append(f"  {status}", style=f"bold {BAD if job.stalled else colour}")
        if job.started:
            ended = job.finished or time.time()
            head.append(f"  {clock(ended - job.started)}", style=FAINT)
        if job.database == "branch" and job.card_id is None:
            head.append(f"  data: {job.data_state or 'on its own branch'}", style=MUTED)
        parts.append(head)
        progress = job.progress
        route = Text("   ")
        for i, phase in enumerate(progress.phases):
            if i:
                route.append(" ━ ", style=OK)
            current = i == len(progress.phases) - 1 and job.status == "running"
            route.append(
                phase.name.upper() if current else phase.name,
                style=f"bold {ACCENT}" if current else OK,
            )
        if progress.phases:
            parts.append(route)
        for phase in progress.phases[-3:]:
            line = Text("   ")
            line.append_text(bar(phase.fraction, 24, OK if phase.fraction == 1 else ACCENT))
            total = f" / {phase.total:,.0f}" if phase.total else ""
            line.append(f"  {phase.done:,.0f}{total} {phase.unit}", style=TEXT)
            if phase.rate:
                line.append(f"  {phase.rate * 60:,.1f}/min", style=MUTED)
            if phase.eta_low is not None and job.status == "running":
                low, high = clock(phase.eta_low), clock(phase.eta_high)
                line.append(f"  ETA {low}" + (f" to {high}" if high != low else ""), style=ACCENT)
            parts.append(line)
        detail = Text("   ")
        if progress.errors:
            rate = f" ({progress.error_rate:.1%} lately)" if progress.error_rate is not None else ""
            detail.append(f"{progress.errors} errors{rate}  ", style=BAD)
        if progress.item:
            detail.append(f"now: {progress.item}  ", style=MUTED)
        if progress.tokens_in:
            detail.append(
                f"its own model calls: {progress.tokens_in:,} in, {progress.tokens_out:,} out",
                style=FAINT,
            )
        if detail.plain.strip():
            parts.append(detail)
        if chosen and job.log_tail:
            for line in job.log_tail.splitlines()[-3:]:
                parts.append(Text(f"   │ {line}", style=FAINT, no_wrap=True, overflow="ellipsis"))
        parts.append(Text(""))
    if snap.declared_jobs:
        declared = Text("declared: ", style=FAINT)
        declared.append(
            "  ".join(f"{slug}: {', '.join(names)}" for slug, names in snap.declared_jobs.items()),
            style=MUTED,
        )
        parts.append(declared)
    parts.append(
        Text(
            "s start a declared job · x stop · l land its data · d discard its data · j/k choose",
            style=FAINT,
        )
    )
    return Group(*parts)


# Epics ------------------------------------------------------------------------------------


STATUS_MARK = {
    "done": ("✓", OK),
    "running": ("◉", ACCENT),
    "review": ("◆", ACCENT),
    "ready": ("○", MUTED),
    "rework": ("↺", WARN),
    "blocked": ("?", WARN),
    "failed": ("✕", BAD),
    "draft": ("·", FAINT),
}


def epics_screen(snap: Snapshot) -> RenderableType:
    if not snap.epics:
        return Text(
            "No epics. A feature too big for one card becomes one: prat card add <project> "
            "<title> --epic. Its planner proposes the parts.",
            style=FAINT,
        )
    parts: list[RenderableType] = []
    for epic in snap.epics:
        total = len(epic.parts)
        head = Text()
        head.append(f"#{epic.id} ", style=f"bold {ACCENT}")
        head.append(f"{epic.title}", style=f"bold {TEXT}")
        head.append(f"  {epic.project}", style=MUTED)
        head.append("  ")
        head.append_text(bar(epic.landed / total if total else None, 16, OK))
        head.append(f"  {epic.landed} of {total} landed", style=OK if epic.finished else TEXT)
        if epic.status == "review":
            head.append("  split waits for your approval", style=WARN)
        parts.append(head)
        rail = Text("   ")
        for i, part in enumerate(epic.parts):
            mark, colour = STATUS_MARK.get(part["status"], ("·", MUTED))
            if i:
                rail.append(" ─ ", style=FAINT)
            rail.append(f"{mark} #{part['id']}", style=colour)
        parts.append(rail)
        for part in epic.parts:
            mark, colour = STATUS_MARK.get(part["status"], ("·", MUTED))
            line = Text(f"   {mark} ", style=colour)
            line.append(f"#{part['id']} {part['title']}", style=TEXT)
            line.append(f"  {part['status']}", style=colour)
            if part["depends_on"]:
                line.append(
                    "  needs " + ", ".join(f"#{d}" for d in part["depends_on"]), style=FAINT
                )
            if part.get("hold"):
                line.append(f"  held: {part['hold']}", style=WARN)
            parts.append(line)
        parts.append(Text(""))
    return Group(*parts)


# Machine ----------------------------------------------------------------------------------


def machine_screen(
    snap: Snapshot, machine: Any, groups: Sequence[tuple[str, list[Any]]], verdict: str
) -> RenderableType:
    head = Text()
    head.append("this machine  ", style=f"bold {TEXT}")
    head.append("CPU ", style=MUTED)
    head.append_text(bar(machine.cpu_percent / 100, 16, WARN if machine.cpu_percent > 85 else OK))
    head.append(f" {machine.cpu_percent:.0f}%   ", style=TEXT)
    head.append("memory ", style=MUTED)
    used = machine.memory_used_percent / 100
    head.append_text(bar(used, 16, WARN if used > 0.85 else OK))
    head.append(
        f" {machine.memory_used_percent:.0f}% ({machine.memory_available_mb / 1024:.1f} GB free)",
        style=TEXT,
    )
    if machine.load:
        head.append(f"   load {machine.load[0]:.1f}", style=MUTED)
    parts: list[RenderableType] = [head, Text(f"governor: {verdict}", style=ACCENT), Text("")]
    table = Table.grid(padding=(0, 2), expand=True)
    for _ in range(5):
        table.add_column(no_wrap=True)
    table.add_column(ratio=1)
    table.add_row(
        *[
            Text(h, style=f"bold {MUTED}")
            for h in ("what", "processes", "CPU", "memory", "running")
        ],
        Text("", style=MUTED),
    )
    for label, processes in groups:
        cpu = sum(p.cpu for p in processes)
        memory = sum(p.memory_mb for p in processes)
        oldest = min((p.started for p in processes), default=None)
        names = ", ".join(sorted({p.name for p in processes}))[:80]
        table.add_row(
            Text(label, style=TEXT),
            Text(str(len(processes)), style=MUTED),
            Text(f"{cpu:.0f}%", style=WARN if cpu > 100 else TEXT),
            Text(f"{memory:,.0f} MB", style=WARN if memory > 2048 else TEXT),
            Text(clock(time.time() - oldest) if oldest else "", style=FAINT),
            Text(names, style=FAINT),
        )
    if not groups:
        table.add_row(
            Text("PRAT has started nothing that is running now.", style=FAINT), *[Text("")] * 5
        )
    parts.append(table)
    waits = snap.review_waits
    if waits:
        parts.append(
            Text(
                f"\nreview queue: {len(waits)} cards, the oldest waiting {max(waits):.1f}h",
                style=WARN if max(waits) > 4 else MUTED,
            )
        )
    return Group(*parts)


# Skills -----------------------------------------------------------------------------------


def skills_screen(
    rows: Sequence[tuple[str, list[tuple[str, str, str, int, float | None, str]]]],
    candidates: Sequence[tuple[str, str, str]],
    selected: int,
) -> RenderableType:
    parts: list[RenderableType] = []
    if candidates:
        parts.append(Text("candidates waiting for you", style=f"bold {WARN}"))
        for index, (slug, name, description) in enumerate(candidates):
            chosen = index == selected
            line = Text("▌ " if chosen else "  ", style=ACCENT)
            line.append(f"{slug}: {name}", style=f"bold {ACCENT}" if chosen else TEXT)
            line.append(f"  {description}", style=MUTED)
            line.no_wrap = True
            line.overflow = "ellipsis"
            parts.append(line)
        parts.append(Text("p promote · r reject · j/k choose\n", style=FAINT))
    for slug, skills in rows:
        parts.append(Text(slug, style=f"bold {TEXT}"))
        table = Table.grid(padding=(0, 2))
        for _ in range(6):
            table.add_column(no_wrap=True)
        table.add_row(
            *[
                Text(h, style=MUTED)
                for h in ("skill", "tier", "phase", "loads", "first review", "advice")
            ]
        )
        for name, tier, phase, loads, rate, advice in skills:
            table.add_row(
                Text(name, style=TEXT),
                Text(tier, style=FAINT),
                Text(phase, style=FAINT),
                Text(str(loads), style=TEXT if loads else FAINT),
                Text(f"{rate:.0%}" if rate is not None else "", style=MUTED),
                Text(advice, style=OK if advice == "helps" else (WARN if advice else FAINT)),
            )
        parts.append(table)
        parts.append(Text(""))
    if not rows and not candidates:
        parts.append(Text("No project has run with skills yet.", style=FAINT))
    return Group(*parts)


# Usage ------------------------------------------------------------------------------------


def usage_screen(snap: Snapshot) -> RenderableType:
    parts: list[RenderableType] = [
        Text("the last day, by model · the one place PRAT shows usage", style=FAINT)
    ]
    if not snap.usage:
        parts.append(Text("No usage recorded yet.", style=FAINT))
        return Group(*parts)
    table = Table.grid(padding=(0, 2))
    for _ in range(7):
        table.add_column(no_wrap=True)
    table.add_row(
        *[
            Text(h, style=f"bold {MUTED}")
            for h in (
                "model",
                "input",
                "output",
                "cache read",
                "cache write",
                "from cache",
                "API value",
            )
        ]
    )
    for row in snap.usage:
        share = row.cache_share
        cache = Text()
        cache.append_text(bar(share, 10, OK))
        cache.append(f" {share:.0%}" if share is not None else "", style=TEXT)
        table.add_row(
            Text(row.model, style=TEXT),
            Text(f"{row.input:,.0f}", style=MUTED),
            Text(f"{row.output:,.0f}", style=MUTED),
            Text(f"{row.cache_read:,.0f}", style=MUTED),
            Text(f"{row.cache_write:,.0f}", style=MUTED),
            cache,
            Text(f"${row.usd:,.2f}", style=MUTED),
        )
    parts.append(table)
    if snap.usage_by_role:
        total = sum(snap.usage_by_role.values()) or 1.0
        parts.append(Text("\nby kind of work", style=f"bold {MUTED}"))
        for role, spent in sorted(snap.usage_by_role.items(), key=lambda kv: kv[1], reverse=True):
            line = Text(f"  {role:<18}", style=TEXT)
            line.append_text(bar(spent / total, 24))
            line.append(f" {spent / total:.0%}", style=MUTED)
            parts.append(line)
    return Group(*parts)
