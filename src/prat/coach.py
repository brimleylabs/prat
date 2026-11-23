"""The coach: reads every run, names what cost it time, and tells the next builder.

A team that is new, or that keeps tripping, is watched closely; one that lands its work first
time is left to get on with it. The coach does three things on each project:

* **Observes** each finished run from PRAT's own records, without a model: the slips it made
  (a file changed through a shell command, line endings rewritten, a neighbouring line retyped,
  a turn ended with failing gates, a real loop, an oversize diff) and whether the reviewer sent
  it back, with the reviewer's fix list.
* **Keeps standing rules**: a slip seen in two of the project's last ten runs (one, while the
  coach is watching closely) becomes a rule the next builder is given, with how often it was
  seen. A rule that stops being broken drops out again on its own. When the reviewer keeps
  sending work back, a short model session (:func:`reflect`) turns the fix lists into at most
  three rules in the project's own terms.
* **Sets its own attention** from how the team is doing: ``close`` for a project with few runs
  or one where under 60% of recent runs came through clean, ``light`` once 85% do, ``normal``
  between. Close attention makes rules sooner and reflects after every run that was sent back.

Rules go to the builder only, as help: they never change PRAT's own rules, and everything the
coach learns from a model is screened like any other proposal. One JSON file per project under
``$PRAT_HOME/coach``.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from prat.improve.research import SCREEN
from prat.policy import Decision, ToolCall, find_secrets
from prat.runtime.base import AgentRuntime, AgentTask, Budget, Outcome
from prat.runtime.session import collect

COACH_DIR = "coach"
RECENT = 10
KEPT_RUNS = 60
MAX_LEARNED = 6
MAX_RULE_CHARS = 260

CLOSE, NORMAL, LIGHT = "close", "normal", "light"
FEW_RUNS = 5
CLOSE_BELOW, LIGHT_FROM = 0.6, 0.85

# A file changed from the shell: in place, by redirect, or by a script written on the spot.
SHELL_EDIT = re.compile(
    r"\bsed\s+(-[a-zA-Z]*i|--in-place)|\bperl\s+-[a-zA-Z]*pi|\btee\s+(-a\s+)?[\w./-]+\.\w+"
    r"|(^|[\s;&|])cat\s*>+\s*[\w./-]+|<<-?\s*['\"]?\w+['\"]?\s*>+\s*[\w./-]+"
    r"|>+\s*[\w./-]+\.\w+\s*<<|\.write_text\(|open\([^)]*['\"][wa]b?['\"]"
)

# The slips the coach knows without a model, and what it tells the next builder about each.
RULES: Mapping[str, tuple[str, str]] = {
    "shell_edit": (
        "changed files through shell commands",
        "Change files with the Edit and Write tools only. Edits made through the shell here "
        "(sed -i, redirects, script one-liners) rewrote whole files' line endings and were cut "
        "off part way.",
    ),
    "line_endings": (
        "rewrote a file's line endings",
        "Leave line endings as they are: never rewrite a whole file to change a few lines of it.",
    ),
    "spacing": (
        "retyped a neighbouring line with different spacing",
        "When you add a line next to an aligned one, anchor your edit on a line you are not "
        "changing and add yours beside it; do not retype the lines around it.",
    ),
    "tests": (
        "removed or switched off tests that were already there",
        "Do not delete, remove or skip a test that was here when you started. If one is wrong "
        "or cannot pass with what the card asks for, leave it and say so in your final message.",
    ),
    "screens": (
        "left a layout fault on a page (a control covered or overlapping, text cut off or "
        "too faint, a target too small)",
        "You cannot see the page, so place every control you add on purpose: say where it "
        "sits and what is beside it at a phone's width and at a desktop's, and reuse the "
        "layout the neighbouring controls already use.",
    ),
    "finish_gates": (
        "ended its turn with the project's checks failing",
        "Run the project's own test command and read its result before you finish: turns here "
        "have ended with those checks failing.",
    ),
    "oversize_diff": (
        "produced a diff over the size limit",
        "Keep the change to what the card asks: no reformatting, no generated or vendored "
        "files, no rewritten files.",
    ),
    "loop": (
        "repeated the same call until it was stopped",
        "When a command or an edit fails twice the same way, stop and read the error instead "
        "of repeating it.",
    ),
}


@dataclass
class Observation:
    run: int
    card: int
    attempt: int = 1
    at: float = 0.0
    # The run reached review and the reviewer did not send it back.
    clean: bool = False
    status: str = ""
    sent_back: bool = False
    slips: dict[str, int] = field(default_factory=dict)
    fixes: list[str] = field(default_factory=list)


def observe(
    *,
    run: int,
    card: int,
    attempt: int,
    status: str,
    events: Iterable[Any],
    critique: Mapping[str, Any] | None = None,
) -> Observation:
    """What one finished run did, from its journal (events with ``kind`` and ``data``) and its
    reviewer's verdict."""
    slips: dict[str, int] = {}

    def saw(kind: str, count: int = 1) -> None:
        slips[kind] = slips.get(kind, 0) + count

    for event in events:
        kind, data = event.kind, event.data or {}
        if kind.endswith("tool_call") and data.get("tool") == "Bash":
            if SHELL_EDIT.search(str((data.get("input") or {}).get("command") or "")):
                saw("shell_edit")
        elif kind == "policy" and data.get("allowed") is False:
            if "Loop detected" in str(data.get("reason") or ""):
                saw("loop")
        elif kind == "execute.line_endings_restored":
            saw("line_endings", len(data.get("paths") or []) or 1)
        elif kind == "verify.slips":
            for slip in ("spacing", "tests"):
                if data.get(slip):
                    saw(slip, len(data[slip]))
        elif kind == "verify.screens" and data.get("new"):
            saw("screens", int(data["new"]))
        elif kind == "verify.finish_check" and data.get("passed") is False:
            saw("finish_gates")
        elif kind == "verify.finding" and data.get("kind") == "oversize_diff":
            saw("oversize_diff")
    verdict = dict(critique or {})
    sent_back = bool(verdict.get("rework"))
    fixes = [" ".join(str(f).split())[:300] for f in (verdict.get("fix_list") or [])][:6]
    return Observation(
        run=run,
        card=card,
        attempt=attempt,
        at=time.time(),
        clean=status == "succeeded" and not sent_back,
        status=status,
        sent_back=sent_back,
        slips=slips,
        fixes=fixes if sent_back else [],
    )


@dataclass
class Learned:
    rule: str
    evidence: str = ""
    since_run: int = 0


@dataclass
class Book:
    """What the coach knows about one project."""

    runs: list[Observation] = field(default_factory=list)
    learned: list[Learned] = field(default_factory=list)
    # The last run whose fix lists a reflection has read.
    reflected_to: int = 0

    @property
    def recent(self) -> list[Observation]:
        return self.runs[-RECENT:]

    def record(self, seen: Observation) -> None:
        self.runs = [r for r in self.runs if r.run != seen.run] + [seen]
        self.runs = self.runs[-KEPT_RUNS:]


def path_for(home: Path, project: str) -> Path:
    return home / COACH_DIR / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', project)}.json"


def load(home: Path, project: str) -> Book:
    try:
        data = json.loads(path_for(home, project).read_text(encoding="utf-8"))
        return Book(
            runs=[Observation(**r) for r in data.get("runs", [])],
            learned=[Learned(**r) for r in data.get("learned", [])],
            reflected_to=int(data.get("reflected_to") or 0),
        )
    except (OSError, ValueError, TypeError):
        return Book()


def save(home: Path, project: str, book: Book) -> None:
    path = path_for(home, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(".tmp")
    scratch.write_text(json.dumps(asdict(book), indent=1), encoding="utf-8")
    scratch.replace(path)


def clean_share(book: Book) -> float | None:
    recent = book.recent
    return sum(1 for r in recent if r.clean) / len(recent) if recent else None


def attention(book: Book) -> str:
    """How closely the coach watches this project: a team earns its distance."""
    share = clean_share(book)
    if share is None or len(book.recent) < FEW_RUNS or share < CLOSE_BELOW:
        return CLOSE
    return LIGHT if share >= LIGHT_FROM and len(book.recent) >= RECENT else NORMAL


@dataclass(frozen=True)
class Rule:
    text: str
    evidence: str


def standing(book: Book) -> list[Rule]:
    """The rules the next builder gets: slips that keep happening, most frequent first, then
    what the coach learned from the reviewer's fix lists."""
    recent = book.recent
    needed = 1 if attention(book) == CLOSE else 2
    counted = [(sum(1 for r in recent if r.slips.get(kind)), kind) for kind in RULES]
    rules = [
        Rule(RULES[kind][1], f"{n} of the last {len(recent)} runs here {RULES[kind][0]}")
        for n, kind in sorted(counted, reverse=True)
        if n >= needed
    ]
    rules += [Rule(item.rule, item.evidence) for item in book.learned]
    return rules


def render(book: Book) -> str:
    """The coach's rules as the builder reads them, or "" when it has none."""
    rules = standing(book)
    if not rules:
        return ""
    lines = [
        "## From the coach",
        "",
        "The coach reads every run on this project. These are what kept costing runs here a "
        "round of review or a failed check; they are help for this card, not new rules of "
        "PRAT's.",
    ]
    lines += [
        f"- {rule.text}" + (f" ({rule.evidence}.)" if rule.evidence else "") for rule in rules
    ]
    return "\n".join(lines)


def note(seen: Observation, book: Book) -> str | None:
    """What the coach writes on the card after a run, or ``None`` when there is nothing to say."""
    if not seen.slips and not seen.sent_back:
        return None
    parts = [f"{RULES[kind][0]} ({count})" for kind, count in seen.slips.items() if kind in RULES]
    lines = []
    if parts:
        lines.append("This run " + "; ".join(parts) + ".")
    if seen.sent_back:
        lines.append("The reviewer sent it back.")
    share = clean_share(book)
    if share is not None:
        lines.append(
            f"On this project {share:.0%} of the last {len(book.recent)} runs came through "
            f"clean; the coach's attention is {attention(book)}."
        )
    return " ".join(lines)


def wants_reflection(book: Book) -> bool:
    """Whether the reviewer's recent fix lists are worth a model reading them now."""
    unread = [r for r in book.runs if r.run > book.reflected_to and r.sent_back and r.fixes]
    watching = attention(book)
    if watching == LIGHT or not unread:
        return False
    return len(unread) >= (1 if watching == CLOSE else 2)


REFLECT_SYSTEM = """\
You are the coach of a team of software agents working on one project. A reviewer sent some of
their recent work back. From the reviewer's fix lists below, write the few standing rules that
would have prevented the repeated problems, for the next agent that builds on this project.

Rules for your rules:
- At most three. Only what the evidence shows more than once, or once but costly. Nothing
  generic ("write good tests"): name the kind of thing this project's reviewer keeps asking for.
- One sentence each, an instruction the agent can act on while it works.
- About how to build well on this project only. Never about a person, a secret, or PRAT's own
  rules and checks.
- If the fix lists show nothing that repeats, answer with an empty list.

Answer with JSON only: {"rules": [{"rule": "...", "evidence": "..."}]}
"""


def _no_tools(call: ToolCall) -> Decision:
    return Decision.deny("the coach has no tools")


def parse_rules(answer: str) -> list[tuple[str, str]]:
    match = re.search(r"\{.*\}", answer or "", re.S)
    if match is None:
        return []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return []
    found: list[tuple[str, str]] = []
    raw = data.get("rules") if isinstance(data, dict) else None
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        rule = " ".join(str(item.get("rule") or "").split())[:MAX_RULE_CHARS]
        evidence = " ".join(str(item.get("evidence") or "").split())[:160]
        if rule and screened(rule) is None:
            found.append((rule, evidence))
    return found[:3]


def screened(text: str) -> str | None:
    """Why a learned rule must not be kept, or ``None``."""
    if find_secrets(text):
        return "looks like a secret"
    for reason, pattern in SCREEN:
        if pattern.search(text):
            return reason
    return None


async def reflect(
    runtime: AgentRuntime,
    book: Book,
    *,
    workdir: Path,
    model: str | None,
    budget_usd: float = 0.3,
) -> list[Learned]:
    """Read the fix lists the coach has not read yet and add what they teach to the book.
    Returns the rules added."""
    unread = [r for r in book.runs if r.run > book.reflected_to and r.sent_back and r.fixes]
    if not unread:
        return []
    evidence = "\n\n".join(
        f"Run {r.run} (card {r.card}) was sent back with:\n" + "\n".join(f"- {f}" for f in r.fixes)
        for r in unread[-6:]
    )
    known = "\n".join(f"- {item.rule}" for item in book.learned) or "(none yet)"
    out = await collect(
        runtime,
        AgentTask(
            prompt=f"{evidence}\n\nRules the team already has:\n{known}",
            system_prompt=REFLECT_SYSTEM,
            workdir=workdir,
            allowed_tools=frozenset(),
            policy=_no_tools,
            budget=Budget(max_usd=budget_usd, max_turns=2),
            model=model,
            metadata={"role": "coach"},
        ),
    )
    book.reflected_to = max(r.run for r in unread)
    if out.outcome is not Outcome.COMPLETED:
        return []
    have = {item.rule.lower() for item in book.learned}
    added = [
        Learned(rule, evidence_text, book.reflected_to)
        for rule, evidence_text in parse_rules(out.final_text)
        if rule.lower() not in have
    ]
    book.learned = (book.learned + added)[-MAX_LEARNED:]
    return added


def summary(book: Book) -> dict[str, Any]:
    share = clean_share(book)
    return {
        "runs": len(book.runs),
        "recent": len(book.recent),
        "clean_share": share,
        "attention": attention(book),
        "rules": [asdict(rule) for rule in standing(book)],
    }


def slip_counts(runs: Sequence[Observation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for run in runs:
        for kind in run.slips:
            counts[kind] = counts.get(kind, 0) + 1
    return counts
