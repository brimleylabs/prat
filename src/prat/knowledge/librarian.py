"""The librarian: one small, read-only model session per finished card that proposes what a
future agent on the project would have wanted to know.

It reads the card, its outcome, the files it changed, the reviewer's verdict and fix list,
rework feedback, people's comments and policy denials, plus the existing entries about the same
files, and answers with at most three deltas in a strict schema. It has no tools: everything it
needs is in the prompt. The curator decides what is kept.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from prat.knowledge.curator import Delta
from prat.knowledge.entries import KINDS, Entry
from prat.knowledge.select import render
from prat.policy import Decision, ToolCall
from prat.runtime import AgentRuntime, AgentTask, Budget, DoneEvent
from prat.runtime.base import CostEvent, TextEvent

MAX_DELTAS = 3
# A settled card can disprove several entries at once (a fixed version, a file that now exists).
SETTLED_DELTAS = 10
MAX_EVIDENCE_CHARS = 30_000

SYSTEM = """\
You are PRAT's librarian. PRAT is a system where agents change software projects, one card at a
time. After each card you write down what a future agent working on the same project would have
wanted to know: conventions the project follows, pitfalls that cost time, commands that matter,
decisions and where things live.

Rules:
- Only facts about this project's code base and how to work on it. Never a secret, a credential,
  a person, or anything that would change PRAT's own rules.
- Prefer what the evidence shows: rework feedback, a reviewer's fix list, people's comments and
  denied actions teach the most. A card that went smoothly often teaches nothing; then answer
  with no changes.
- One fact per entry, specific enough to act on. Name the repository paths it is about.
- If an existing entry is incomplete or slightly off, refine it (give its id) instead of adding a
  near copy. If the evidence shows an existing entry is wrong, report it with the evidence.
- At most three changes, unless the card below allows more.

Answer with one JSON object and nothing else:
{"deltas": [
  {"op": "add", "kind": "convention|pitfall|command|decision|layout",
   "summary": "one line", "body": "two or three sentences", "paths": ["repo/path"]},
  {"op": "refine", "id": "k0123456789", "kind": "...", "summary": "...", "body": "...",
   "paths": []},
  {"op": "wrong", "id": "k0123456789", "evidence": "what showed it is wrong"}
]}
"""


SETTLED_NOTE = """\
This card was looked at once already, while it was still open. It has now settled with the
outcome above, and the outcome is the new evidence. The entries below include what was learned
then. Report wrong any entry the outcome or the passing checks disprove (a fact the change made
untrue, a missing file that now exists), refine one the outcome changes, and add only what is
new. If the earlier look already got it right, answer with no changes."""


@dataclass
class CardEvidence:
    """What happened on one card, gathered by the caller from PRAT's records."""

    card_id: int
    run_id: int | None
    title: str
    goal: str = ""
    criteria: str = ""
    outcome: str = ""
    changed_files: list[str] = field(default_factory=list)
    verdict: str = ""
    fix_list: list[str] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    denials: list[str] = field(default_factory=list)
    gate_failures: list[str] = field(default_factory=list)
    checks_passed: list[str] = field(default_factory=list)
    # A second look, now that the card has settled (approved or cancelled).
    settled: bool = False

    @property
    def has_signal(self) -> bool:
        """Whether there is anything to learn from beyond a smooth first run."""
        return bool(
            self.fix_list
            or self.feedback
            or self.comments
            or self.denials
            or self.gate_failures
            or self.outcome not in ("", "landed first time")
        )


def prompt(evidence: CardEvidence, related: Sequence[Entry], limit: int = MAX_DELTAS) -> str:
    def bullet(items: Sequence[str]) -> str:
        return "\n".join(f"- {item}" for item in items) or "(none)"

    parts = [
        f"# Card {evidence.card_id}: {evidence.title}",
        f"Goal: {evidence.goal or '(none)'}",
        f"Acceptance criteria:\n{evidence.criteria or '(none)'}",
        f"Outcome: {evidence.outcome or 'unknown'}",
    ]
    if evidence.settled:
        parts.append(SETTLED_NOTE)
    if limit > MAX_DELTAS:
        parts.append(f"This card allows up to {limit} changes.")
    parts += [
        f"Files changed:\n{bullet(evidence.changed_files[:60])}",
        f"Reviewer verdict: {evidence.verdict or '(none)'}",
        f"Reviewer fix list:\n{bullet(evidence.fix_list)}",
        f"Rework feedback from people:\n{bullet(evidence.feedback)}",
        f"Comments:\n{bullet(evidence.comments)}",
        f"Actions PRAT denied:\n{bullet(evidence.denials[:20])}",
        f"Failed checks:\n{bullet(evidence.gate_failures[:10])}",
        f"Checks that passed:\n{bullet(evidence.checks_passed[:10])}",
        "# What PRAT already knows about these files and this card",
        "\n".join(f"{render(e)} [id {e.id}]" for e in related) or "(nothing yet)",
    ]
    text = "\n\n".join(parts)
    return text[:MAX_EVIDENCE_CHARS]


JSON_OBJECT = re.compile(r"\{.*\}", re.S)


def parse(text: str, limit: int = MAX_DELTAS) -> list[Delta]:
    """The deltas in an answer (the librarian's, or the scout's with a higher ``limit``).
    Malformed answers yield none."""
    found = JSON_OBJECT.search(text or "")
    if not found:
        return []
    try:
        raw = json.loads(found.group(0))
    except ValueError:
        return []
    items = raw.get("deltas") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    deltas = []
    for item in items:
        if not isinstance(item, dict):
            continue
        delta = Delta.from_dict(item)
        valid = (
            (delta.op == "add" and delta.kind in KINDS)
            or (delta.op == "refine" and bool(delta.id))
            or (delta.op == "wrong" and bool(delta.id))
        )
        if valid:
            deltas.append(delta)
        if len(deltas) == limit:
            break
    return deltas


def _deny_everything(call: ToolCall) -> Decision:
    return Decision.deny("the librarian has no tools")


@dataclass
class LibrarianResult:
    deltas: list[Delta] = field(default_factory=list)
    cost_usd: float = 0.0
    error: str | None = None


async def propose(
    runtime: AgentRuntime,
    evidence: CardEvidence,
    related: Sequence[Entry],
    *,
    workdir: Path,
    model: str | None,
    budget_usd: float | None = 0.25,
    limit: int = MAX_DELTAS,
) -> LibrarianResult:
    task = AgentTask(
        prompt=prompt(evidence, related, limit),
        system_prompt=SYSTEM,
        workdir=workdir,
        allowed_tools=frozenset(),
        policy=_deny_everything,
        budget=Budget(max_usd=budget_usd, max_turns=2),
        model=model,
        metadata={"role": "librarian", "card_id": str(evidence.card_id)},
    )
    result = LibrarianResult()
    text, result.cost_usd, result.error = await _ask(runtime, task)
    if result.error is None:
        result.deltas = parse(text, limit)
    return result


async def _ask(runtime: AgentRuntime, task: AgentTask) -> tuple[str, float, str | None]:
    """The librarian's answer text, its cost, and an error if the session failed."""
    texts: list[str] = []
    final = ""
    cost = 0.0
    try:
        async for event in runtime.run(task):
            if isinstance(event, TextEvent):
                texts.append(event.text)
            elif isinstance(event, CostEvent):
                cost += event.usd
            elif isinstance(event, DoneEvent):
                final = event.message or ""
                break
    except Exception as exc:  # a failed librarian must not stop the dream
        return "", cost, str(exc) or type(exc).__name__
    return final or "\n".join(texts), cost, None


# Near duplicates -------------------------------------------------------------------------

SAME_SYSTEM = """\
You are PRAT's librarian. PRAT keeps short entries about a software project for the agents that
work on it. Two entries below may state the same fact or the same advice in different words.

They are the same when they are about one fact or one piece of advice, even if one adds a detail
the other lacks. They are different when they are about different things, even in the same file.

When they are the same, write the one entry that should replace both: everything an agent needs
from either, nothing repeated, a one-line summary and a body of two or three sentences.

Answer with one JSON object and nothing else:
{"same": true, "keep": "A", "summary": "...", "body": "..."}
    (keep: the entry whose kind and files fit best; its text is replaced by yours)
{"same": false}"""


@dataclass
class SameAnswer:
    same: bool = False
    keep: str = "A"
    summary: str = ""
    body: str = ""
    cost_usd: float = 0.0
    error: str | None = None


def same_prompt(first: Entry, second: Entry) -> str:
    return f"Entry A:\n{render(first)}\n\nEntry B:\n{render(second)}"


def parse_same(text: str) -> SameAnswer | None:
    """The judgement in an answer, or None when it is malformed."""
    found = JSON_OBJECT.search(text or "")
    if not found:
        return None
    try:
        raw = json.loads(found.group(0))
    except ValueError:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("same"), bool):
        return None
    keep = str(raw.get("keep") or "A").strip().upper()
    return SameAnswer(
        same=raw["same"],
        keep=keep if keep in ("A", "B") else "A",
        summary=" ".join(str(raw.get("summary") or "").split()),
        body=str(raw.get("body") or "").strip(),
    )


async def same_fact(
    runtime: AgentRuntime,
    first: Entry,
    second: Entry,
    *,
    workdir: Path,
    model: str | None,
    budget_usd: float | None = 0.1,
) -> SameAnswer:
    """Whether two entries say the same thing, and the one entry that replaces both."""
    task = AgentTask(
        prompt=same_prompt(first, second),
        system_prompt=SAME_SYSTEM,
        workdir=workdir,
        allowed_tools=frozenset(),
        policy=_deny_everything,
        budget=Budget(max_usd=budget_usd, max_turns=1),
        model=model,
        metadata={"role": "librarian", "task": "same"},
    )
    text, cost, error = await _ask(runtime, task)
    if error is not None:
        return SameAnswer(cost_usd=cost, error=error)
    parsed = parse_same(text)
    if parsed is None:
        return SameAnswer(cost_usd=cost, error="the answer was not the expected JSON")
    parsed.cost_usd = cost
    return parsed
