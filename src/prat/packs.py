"""Fact packs: what a builder would otherwise spend its first minutes finding out, worked out
while the card waits.

A card in the backlog is researched before anyone asks for it to be built. One read-only
session explores the code for that card and writes down where things are, what to imitate and
what would go wrong: the data model involved, the files to touch and to leave alone, the
signatures the change calls, the call sites, the closest existing feature, how tests are run
here, and the traps. A second, independent session then checks every statement against the
code and corrects or removes what does not hold: a pack that is wrong is worse than none,
because the builder trusts it.

When the card is built the pack is checked once more, without a model (:func:`preflight`): a
card whose text changed since the research is researched again, and a statement about a file
that changed since is left out. What remains goes to the builder, and only the builder: the
reviewer judges the work against the card, never against the pack.

Packs are plain JSON files under ``$PRAT_HOME/packs``, one per card.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from prat.domain import Autonomy
from prat.policy import Policy
from prat.runtime.base import READ_TOOLS, AgentRuntime, AgentTask, Budget, Outcome
from prat.runtime.session import collect

PACKS_DIR = "packs"
MAX_PACK_CHARS = 24_000
# More of the pack than this about files that changed since: research the card again.
STALE_SHARE = 0.4

HEADING = "## Facts prepared for this card"
PREAMBLE = (
    "A research pass explored the code for this card before you started, and a second pass "
    "checked each statement against the code. Line numbers are for the commit you start from. "
    "Use them to start writing at once: you should not need to search for what is listed here. "
    "They are help, not requirements: the card says what to build, and if the code in front of "
    "you disagrees with a statement here, the code is right."
)

SECTIONS = """\
- Data model: the tables, columns, keys and relationships (or the data structures) involved.
- How the data model is changed in this project (migrations, schema files, versions), when the
  card needs that.
- Files to touch.
- Files to leave alone, and why.
- Signatures the change calls: exact parameters, with line numbers.
- Call sites: where the code to change is used today.
- Closest existing feature to imitate: file and line ranges for each part of it.
- Tests: how tests here are written, and the exact command that runs only the relevant ones.
- Traps: anything in this code base that makes an obvious implementation of this card wrong,
  including where the card's requirements conflict with what the code or its comments suggest.
- Decisions: the few design choices you recommend where the card leaves one open, each marked
  as a decision and not a fact."""

RESEARCH_SYSTEM = f"""\
You prepare a fact pack for the engineer who will implement the card below in this repository.
You implement nothing and change no files. Explore the code with your read-only tools, think
the problem through once, and write the pack as your final answer in Markdown.

The pack's purpose: the implementer starts writing at once, without searching or reading files
to find their way, and without falling into this code base's traps.

Rules:
- Facts only, each checked against the code in front of you and cited as `path/to/file.ext:LINE`
  (or `path/to/file.ext:FROM-TO`), with the path from the repository root. Never guess. What you
  could not verify, leave out.
- Under 150 lines. Dense and specific to this card; no general advice.
- A claim about what code does ("X drops empty values, so...") only after following the data
  through the functions involved.
- Sections, in this order, leaving out any that does not apply to this card:
{SECTIONS}
"""

VERIFY_SYSTEM = """\
You check a fact pack that another agent wrote for the engineer who will implement the card
below in this repository. A wrong statement in the pack is worse than a missing one: the
engineer will trust it. You change no files; use your read-only tools.

Go through the pack statement by statement. For each one open the code it cites and decide:
- True as written: keep it.
- Wrong, or true of the cited line but misleading about what actually happens (follow the data
  through the functions involved before you accept a claim about behaviour): correct it if you
  can state the true fact with its file and line, otherwise delete it.
- Not checkable from the code: delete it.
Give most attention to the traps and to any statement of the form "X does Y, so you must Z":
trace X yourself. Add no advice of your own beyond corrections. Keep every citation in the form
`path/to/file.ext:LINE`.

Your final answer is the corrected pack in full, in the same sections and Markdown, followed by
a last section headed exactly "## Verification notes" that lists each statement you corrected or
removed, one line each with the reason. Nothing else.
"""

NOTES_HEADING = re.compile(r"^##\s+Verification notes\s*$", re.M | re.I)
# `coverage/sqldata.py:60-121`, tests/test_data.py:363, src/app/Screen.tsx:12
CITED = re.compile(r"(?<![\w/.-])((?:[\w.@+-]+/)*[\w.@+-]+\.[A-Za-z][\w]{0,7}):\d+(?:-\d+)?")
# A line number on its own, standing for the file named before it: (`:669`)
BARE_LINE = re.compile(r"(?<![\w/.])`?:\d+(?:-\d+)?`?")
BULLET = re.compile(r"^[-*]\s")


def card_hash(title: str, goal: str, acceptance_criteria: str) -> str:
    text = "\n\x1f".join(part.strip() for part in (title, goal, acceptance_criteria))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


@dataclass
class Pack:
    card_id: int
    card_hash: str
    commit: str
    text: str
    # "verified": researched and checked. "unverified": the check did not finish, and the pack
    # is not given to a builder. "failed": the research itself produced nothing usable.
    status: str = "verified"
    corrections: int = 0
    notes: str = ""
    research_seconds: float = 0.0
    verify_seconds: float = 0.0
    cost_usd: float = 0.0
    built_at: float = 0.0
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.status == "verified" and bool(self.text.strip())


def path_for(home: Path, card_id: int) -> Path:
    return home / PACKS_DIR / f"card-{card_id}.json"


def load(home: Path, card_id: int) -> Pack | None:
    path = path_for(home, card_id)
    try:
        return Pack(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def save(home: Path, pack: Pack) -> Path:
    path = path_for(home, pack.card_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(".tmp")
    scratch.write_text(json.dumps(asdict(pack), indent=2), encoding="utf-8")
    scratch.replace(path)
    return path


def card_text(title: str, goal: str, acceptance_criteria: str, notes: str = "") -> str:
    parts = [f"# Card: {title.strip()}", goal.strip()]
    if acceptance_criteria.strip():
        parts += ["## Acceptance criteria", acceptance_criteria.strip()]
    if notes.strip():
        parts += ["## About this project", notes.strip()]
    return "\n\n".join(part for part in parts if part)


def split_notes(answer: str) -> tuple[str, str]:
    """A verifier's answer as (the corrected pack, its notes on what it changed)."""
    match = NOTES_HEADING.search(answer)
    if match is None:
        return answer.strip(), ""
    return answer[: match.start()].strip(), answer[match.end() :].strip()


def _task(
    system: str,
    prompt: str,
    workdir: Path,
    model: str | None,
    budget_usd: float,
    role: str,
    card: int,
) -> AgentTask:
    return AgentTask(
        prompt=prompt,
        system_prompt=system,
        workdir=workdir,
        allowed_tools=READ_TOOLS,
        policy=Policy(Autonomy.READ_ONLY, workdir),
        budget=Budget(max_usd=budget_usd),
        model=model,
        metadata={"role": role, "card_id": str(card)},
    )


async def build(
    runtime: AgentRuntime,
    *,
    card_id: int,
    title: str,
    goal: str,
    acceptance_criteria: str,
    notes: str,
    workdir: Path,
    commit: str,
    model: str | None,
    verifier_model: str | None = None,
    budget_usd: float = 2.0,
    on_event: Callable[[Any], Any] | None = None,
) -> Pack:
    """Research the card in ``workdir`` (a checkout at ``commit``), then check the result."""
    card = card_text(title, goal, acceptance_criteria, notes)
    pack = Pack(
        card_id=card_id,
        card_hash=card_hash(title, goal, acceptance_criteria),
        commit=commit,
        text="",
        status="failed",
        built_at=time.time(),
    )
    started = time.monotonic()
    research = await collect(
        runtime,
        _task(RESEARCH_SYSTEM, card, workdir, model, budget_usd / 2, "researcher", card_id),
        on_event=on_event,
    )
    pack.research_seconds = round(time.monotonic() - started, 1)
    pack.cost_usd = research.cost_usd
    draft = research.final_text.strip()
    if research.outcome is not Outcome.COMPLETED or not draft:
        pack.reason = (
            f"research: {research.outcome or 'no result'} {research.message or ''}".strip()
        )
        return pack
    pack.text, pack.status = draft[:MAX_PACK_CHARS], "unverified"
    started = time.monotonic()
    check = await collect(
        runtime,
        _task(
            VERIFY_SYSTEM,
            f"{card}\n\n# The pack to check\n\n{draft}",
            workdir,
            verifier_model or model,
            budget_usd / 2,
            "researcher",
            card_id,
        ),
        on_event=on_event,
    )
    pack.verify_seconds = round(time.monotonic() - started, 1)
    pack.cost_usd = round(pack.cost_usd + check.cost_usd, 4)
    checked, notes_text = split_notes(check.final_text)
    if check.outcome is not Outcome.COMPLETED or not checked:
        pack.reason = f"check: {check.outcome or 'no result'} {check.message or ''}".strip()
        return pack
    pack.text, pack.notes, pack.status = checked[:MAX_PACK_CHARS], notes_text, "verified"
    pack.corrections = sum(1 for line in notes_text.splitlines() if BULLET.match(line.strip()))
    return pack


def cited_files(text: str) -> set[str]:
    return {match.group(1) for match in CITED.finditer(text)}


def _blocks(text: str) -> list[list[str]]:
    """The pack as blocks: a top-level bullet with the lines under it, or any other line."""
    blocks: list[list[str]] = []
    for line in text.splitlines():
        under = bool(blocks) and BULLET.match(blocks[-1][0]) is not None
        if under and line.startswith((" ", "\t")) and line.strip():
            blocks[-1].append(line)
        else:
            blocks.append([line])
    return blocks


@dataclass
class Preflight:
    text: str | None
    dropped: int = 0
    kept: int = 0
    reason: str = ""


def preflight(pack: Pack | None, *, current_hash: str, changed: Iterable[str] | None) -> Preflight:
    """What of a pack still holds for a run that starts now. ``changed`` is the files that
    differ between the commit the pack was researched at and the commit the run starts from
    (``None`` when that could not be worked out, which makes the pack unusable)."""
    if pack is None:
        return Preflight(None, reason="no pack")
    if not pack.usable:
        return Preflight(None, reason=f"pack is {pack.status}")
    if pack.card_hash != current_hash:
        return Preflight(None, reason="the card changed since it was researched")
    if changed is None:
        return Preflight(None, reason="could not compare the code with the researched commit")
    moved = {path.replace("\\", "/") for path in changed}
    if not moved & cited_files(pack.text):
        blocks = sum(1 for block in _blocks(pack.text) if BULLET.match(block[0]))
        return Preflight(pack.text, kept=blocks)
    kept: list[str] = []
    dropped = held = 0
    about: str | None = None
    for block in _blocks(pack.text):
        stale = False
        for line in block:
            # A line number on its own stands for the file named last before it.
            for match in CITED.finditer(line):
                about = match.group(1)
                stale = stale or about in moved
            if about in moved and BARE_LINE.search(line) is not None:
                stale = True
        if not BULLET.match(block[0]):
            kept.extend(block)  # headings and prose stay; they cite nothing on their own
        elif stale:
            dropped += 1
        else:
            held += 1
            kept.extend(block)
    if dropped / max(dropped + held, 1) > STALE_SHARE:
        return Preflight(None, dropped=dropped, kept=held, reason="too much of the code changed")
    note = (
        f"\n\nLeft out, because the files they are about changed after the research: {dropped} "
        f"statement{'' if dropped == 1 else 's'}."
    )
    return Preflight("\n".join(kept).strip() + note, dropped=dropped, kept=held)


def render(text: str) -> str:
    return f"{HEADING}\n\n{PREAMBLE}\n\n{text.strip()}"
