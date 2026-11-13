"""Lessons: a short explanation, written for the engineer, of each card that reaches review.

Agents write more of the code every month, and an engineer who stops reading it loses the
skill that makes their review worth anything. Each lesson has three layers: the general idea
behind the change, what actually happened on this card (with excerpts from the real diff), and
why the change was made this way, with the alternatives. Where they apply it adds where the
change fits in the project and the trap: what went wrong on the way (a fix list, a failed gate,
an action PRAT stopped), which only PRAT knows.

One session per card, with no tools, like the librarian. PRAT then checks the answer: every
code excerpt must appear word for word in the diff or in the file at the head commit, or it is
dropped. Lessons are stored as card comments of kind ``lesson``, versioned with the rest of the
card. Nothing records who reads them.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from prat import store
from prat.db import Comment, Database, Run
from prat.db.models import AppSetting
from prat.domain import CommentAuthor
from prat.git import Git
from prat.knowledge.entries import KnowledgeBase
from prat.knowledge.librarian import JSON_OBJECT, _ask, _deny_everything
from prat.knowledge.maintain import gather, related_entries
from prat.knowledge.select import render as render_entry
from prat.policy.secrets import redact
from prat.project_detect import suggest
from prat.runtime import AgentRuntime, AgentTask, Budget

log = logging.getLogger(__name__)

KIND = "lesson"
FAMILIARITY_KEY = "lessons.familiarity"
LEVELS = ("new", "working", "expert")
DEFAULT_LEVEL = "working"
MAX_DIFF_CHARS = 40_000
MAX_EXCERPTS = 4

SYSTEM = """\
You are PRAT's teacher. PRAT is a system where AI agents change software projects one card at
a time and people review, merge and push. You write a short lesson for the engineer who will
review this card, so they understand what was built, why it was built that way, and the idea
behind it. The point is to keep the engineer's own skills sharp while agents write the code:
months from now, when someone asks "why does it work like this?", the engineer should know.

Write for a capable engineer. Be concrete and plain. No praise, no filler, no marketing.

The lesson has these parts:
- idea: the general technique, pattern or concept this card turns on, independent of this
  code base (for example half-open ranges, recursion and its base case, idempotent migrations,
  checking permissions at the API rather than the UI). Three to six sentences of theory.
- happened: what the problem or request was and what the change does, in plain words.
- excerpts: two to four short excerpts copied exactly from the diff below (lines from the new
  side, or removed lines when the removal is the point), each with the file path and a note on
  what it does. Copy the code exactly, character for character, without the leading +, - or
  space of the diff. Never invent or tidy code: an excerpt that is not in the diff is dropped.
- why: how this change applies the idea. Why this approach; whether it is the standard answer
  to this kind of problem or where it departs from it.
- alternatives: one to three other ways it could have been done, each with its trade-off.
- fits: where this code sits in the project and what depends on it, from the evidence. Leave it
  empty when the evidence does not say.
- trap: what went wrong on the way, from the evidence (a reviewer's fix list, rework feedback,
  a failed check, an action PRAT denied), and what it teaches. Leave it empty when the card went
  smoothly; never invent one.
- question: one question that makes the engineer think about the code (what would break if
  this changed?), and its answer.
- tags: one to three short names for the ideas, lower case (for example "off-by-one").

Depth follows the engineer's familiarity with the stack, given below. "new": also explain the
stack's idioms the change relies on, briefly, where they appear. "working": assume the stack's
basics. "expert": keep the idea short and spend the words on the why and the alternatives.

Answer with one JSON object and nothing else:
{"title": "a short title for the lesson",
 "idea": "...", "happened": "...",
 "excerpts": [{"file": "path/in/repo", "code": "exact lines", "note": "..."}],
 "why": "...", "alternatives": ["..."], "fits": "...", "trap": "...",
 "question": "...", "answer": "...", "tags": ["..."]}
"""


@dataclass
class LessonEvidence:
    """What the teacher is given about one run."""

    card_id: int
    run_id: int
    title: str
    goal: str = ""
    criteria: str = ""
    stack: str = ""
    familiarity: str = DEFAULT_LEVEL
    diff: str = ""
    builder_message: str = ""
    plan: str = ""
    verdict: str = ""
    review_summary: str = ""
    fix_lists: list[str] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    denials: list[str] = field(default_factory=list)
    gate_failures: list[str] = field(default_factory=list)
    knowledge: str = ""
    head_commit: str = ""


@dataclass
class Excerpt:
    file: str
    code: str
    note: str = ""


@dataclass
class Lesson:
    title: str = ""
    idea: str = ""
    happened: str = ""
    excerpts: list[Excerpt] = field(default_factory=list)
    why: str = ""
    alternatives: list[str] = field(default_factory=list)
    fits: str = ""
    trap: str = ""
    question: str = ""
    answer: str = ""
    tags: list[str] = field(default_factory=list)
    # Filled by the check, not the model.
    checked: bool = True
    dropped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Lesson:
        def text(key: str) -> str:
            return str(data.get(key) or "").strip()

        def texts(key: str, limit: int) -> list[str]:
            items = data.get(key) or []
            if not isinstance(items, list):
                return []
            return [str(x).strip() for x in items if str(x).strip()][:limit]

        excerpts = []
        for item in data.get("excerpts") or []:
            if isinstance(item, dict) and str(item.get("code") or "").strip():
                excerpts.append(
                    Excerpt(
                        file=str(item.get("file") or "").strip(),
                        code=str(item.get("code")).strip("\n"),
                        note=str(item.get("note") or "").strip(),
                    )
                )
        return cls(
            title=text("title"),
            idea=text("idea"),
            happened=text("happened"),
            excerpts=excerpts[:MAX_EXCERPTS],
            why=text("why"),
            alternatives=texts("alternatives", 3),
            fits=text("fits"),
            trap=text("trap"),
            question=text("question"),
            answer=text("answer"),
            tags=[t.lower() for t in texts("tags", 3)],
            checked=bool(data.get("checked", True)),
            dropped=int(data.get("dropped") or 0),
        )


# Familiarity -------------------------------------------------------------------------------


def familiarity(db: Database) -> dict[str, str]:
    """The engineer's own setting per stack (new, working or expert). Private: it only shapes
    how much a lesson explains."""
    with db.session() as session:
        row = session.get(AppSetting, FAMILIARITY_KEY)
        value = row.value if row is not None else None
    if not isinstance(value, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in value.items() if v in LEVELS}


def set_familiarity(db: Database, stack: str, level: str) -> None:
    if level not in LEVELS:
        raise ValueError(f"familiarity must be one of {', '.join(LEVELS)}")
    current = familiarity(db)
    current[stack.lower()] = level
    with db.session() as session:
        row = session.get(AppSetting, FAMILIARITY_KEY)
        if row is None:
            session.add(AppSetting(key=FAMILIARITY_KEY, value=current))
        else:
            row.value = current


# Evidence ----------------------------------------------------------------------------------


def collect(db: Database, home: Path, run_id: int) -> LessonEvidence:
    """Everything PRAT recorded about a run and the earlier runs of its card."""
    levels = familiarity(db)
    with db.session() as session:
        run = session.get(Run, run_id)
        if run is None:
            raise LookupError(f"no run {run_id}")
        card = run.card
        project = card.project
        agent = Path(project.agent_path)
        evidence = LessonEvidence(
            card_id=card.id,
            run_id=run.id,
            title=card.title,
            goal=card.goal or "",
            criteria=card.acceptance_criteria or "",
            head_commit=run.head_commit or "",
        )
        runs = sorted(card.runs, key=lambda r: r.id)
        base = next((r.base_commit for r in runs if r.base_commit), None)
        files: list[str] = []
        for earlier in runs:
            if earlier.id > run.id:
                break
            found, _given = gather(session, earlier)
            files = found.changed_files or files
            if earlier.id != run.id:
                evidence.fix_lists += [f"(run {earlier.attempt}) {x}" for x in found.fix_list]
            evidence.feedback += found.feedback
            evidence.denials += found.denials
            evidence.gate_failures += found.gate_failures
            if earlier.id == run.id:
                evidence.verdict = found.verdict
        for step in run.steps:
            detail = step.detail or {}
            if step.name == "execute":
                evidence.builder_message = str(detail.get("message") or "")
            elif step.name == "plan":
                evidence.plan = str(detail.get("plan") or "")
            elif step.name == "critique":
                evidence.review_summary = str(detail.get("summary") or "")
        slug = project.slug
    if agent.is_dir():
        stacks = suggest(agent).stacks
        evidence.stack = ", ".join(stacks)
        level = next((levels[s.lower()] for s in stacks if s.lower() in levels), None)
        evidence.familiarity = level or DEFAULT_LEVEL
        if base and evidence.head_commit:
            shown = Git(agent).run("diff", f"{base}..{evidence.head_commit}", check=False)
            if shown.ok:
                evidence.diff = shown.stdout
    kb = KnowledgeBase(home, slug)
    if kb.entries_dir.is_dir():
        related = related_entries(kb.entries(), files, 6, card_id=evidence.card_id)
        evidence.knowledge = "\n".join(render_entry(e) for e in related)
    return evidence


def prompt(evidence: LessonEvidence) -> str:
    def bullet(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) or "(none)"

    diff = evidence.diff
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n(the rest of the diff is left out)"
    parts = [
        f"# Card {evidence.card_id}: {evidence.title}",
        f"Goal:\n{evidence.goal or '(none)'}",
        f"Acceptance criteria:\n{evidence.criteria or '(none)'}",
        f"Stack: {evidence.stack or 'unknown'}",
        f"The engineer's familiarity with this stack: {evidence.familiarity}",
        f"The plan, if there was one:\n{evidence.plan[:4000] or '(none)'}",
        f"The builder's own account of what it did:\n{evidence.builder_message[:4000] or '(none)'}",
        f"Reviewer verdict: {evidence.verdict or '(none)'}\n{evidence.review_summary[:2000]}",
        f"Fix lists from earlier runs of this card:\n{bullet(evidence.fix_lists)}",
        f"Rework feedback from people:\n{bullet([f[:1500] for f in evidence.feedback])}",
        f"Failed checks along the way:\n{bullet(evidence.gate_failures[:10])}",
        f"Actions PRAT denied:\n{bullet(evidence.denials[:10])}",
        f"What PRAT knows about this part of the project:\n{evidence.knowledge or '(nothing yet)'}",
        f"# The diff\n{diff or '(empty)'}",
    ]
    return redact("\n\n".join(parts))


def parse(text: str) -> Lesson | None:
    found = JSON_OBJECT.search(text or "")
    if not found:
        return None
    try:
        raw = json.loads(found.group(0))
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    lesson = Lesson.from_dict(raw)
    if not (lesson.idea and lesson.happened and lesson.why):
        return None
    return lesson


# The check ---------------------------------------------------------------------------------

DIFF_FILE = re.compile(r"^\+\+\+ b/(.+)$")


def _lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]


def diff_sides(diff: str) -> dict[str, tuple[str, str]]:
    """Per file, the old side and the new side of the diff's hunks, as text."""
    sides: dict[str, tuple[list[str], list[str]]] = {}
    current: tuple[list[str], list[str]] | None = None
    for line in diff.replace("\r\n", "\n").split("\n"):
        if line.startswith("diff --git "):
            current = None
            continue
        named = DIFF_FILE.match(line)
        if named:
            current = sides.setdefault(named.group(1), ([], []))
            continue
        if current is None or line.startswith(("--- ", "+++ ", "@@", "\\ ")):
            if line.startswith("@@") and current is not None:
                current[0].append("")
                current[1].append("")
            continue
        old, new = current
        if line.startswith("+"):
            new.append(line[1:].rstrip())
        elif line.startswith("-"):
            old.append(line[1:].rstrip())
        else:
            old.append(line[1:].rstrip())
            new.append(line[1:].rstrip())
    return {name: ("\n".join(o), "\n".join(n)) for name, (o, n) in sides.items()}


def _contains(haystack: str, code: str) -> bool:
    wanted = "\n".join(_lines(code)).strip("\n")
    return bool(wanted) and wanted in haystack


def check(lesson: Lesson, diff: str, file_at_head: Callable[[str], str | None]) -> Lesson:
    """Drop every excerpt that is not word for word in the diff or in its file at the head
    commit. A lesson whose excerpts were all dropped is marked unchecked."""
    sides = diff_sides(diff)
    kept = []
    for excerpt in lesson.excerpts:
        name = excerpt.file.strip().removeprefix("./")
        current = file_at_head(name) if name else None
        # The file may be named loosely, so any file's hunks count too.
        places = [
            *sides.get(name, ("", "")),
            "\n".join(_lines(current)) if current is not None else "",
            *(side for pair in sides.values() for side in pair),
        ]
        if any(_contains(place, excerpt.code) for place in places):
            kept.append(excerpt)
        else:
            lesson.dropped += 1
    lesson.excerpts = kept
    lesson.checked = bool(kept) or not lesson.dropped
    return lesson


# Writing -----------------------------------------------------------------------------------


@dataclass
class LessonResult:
    lesson: Lesson | None = None
    cost_usd: float = 0.0
    error: str | None = None
    comment_id: int | None = None


async def compose(
    runtime: AgentRuntime,
    evidence: LessonEvidence,
    *,
    workdir: Path,
    model: str | None,
    file_at_head: Callable[[str], str | None],
    budget_usd: float | None = 0.6,
) -> LessonResult:
    """Ask for the lesson and check it; asks once more when no excerpt survives the check."""
    result = LessonResult()
    for _attempt in range(2):
        task = AgentTask(
            prompt=prompt(evidence),
            system_prompt=SYSTEM,
            workdir=workdir,
            allowed_tools=frozenset(),
            policy=_deny_everything,
            budget=Budget(max_usd=budget_usd, max_turns=2),
            model=model,
            metadata={"role": "teacher", "card_id": str(evidence.card_id)},
        )
        text, cost, error = await _ask(runtime, task)
        result.cost_usd += cost
        if error is not None:
            result.error = error
            return result
        lesson = parse(text)
        if lesson is None:
            result.error = "the answer was not a lesson"
            continue
        result.error = None
        result.lesson = check(lesson, evidence.diff, file_at_head)
        if result.lesson.checked:
            break
    return result


def _reader(agent: Path, commit: str) -> Callable[[str], str | None]:
    def read(path: str) -> str | None:
        if not commit or not agent.is_dir():
            return None
        shown = Git(agent).run("show", f"{commit}:{path}", check=False)
        return shown.stdout if shown.ok else None

    return read


async def teach(
    db: Database,
    home: Path,
    runtime: AgentRuntime,
    run_id: int,
    *,
    model: str | None,
    budget_usd: float | None = 0.6,
) -> LessonResult:
    """Write, check and store the lesson for one run. Never raises: a lesson that cannot be
    written leaves the card as it was."""
    try:
        evidence = collect(db, home, run_id)
        if not evidence.diff.strip():
            return LessonResult(error="the run changed nothing to teach from")
        with db.session() as session:
            run = session.get(Run, run_id)
            agent = Path(run.card.project.agent_path) if run is not None else home
        result = await compose(
            runtime,
            evidence,
            workdir=home,
            model=model,
            file_at_head=_reader(agent, evidence.head_commit),
            budget_usd=budget_usd,
        )
        if result.lesson is None:
            return result
        data = {
            **result.lesson.to_dict(),
            "head_commit": evidence.head_commit,
            "model": model or "",
            "cost_usd": round(result.cost_usd, 6),
            "familiarity": evidence.familiarity,
            "stack": evidence.stack,
        }
        with db.session() as session:
            card = store.get_card(session, evidence.card_id)
            comment = store.add_comment(
                session,
                card,
                render(result.lesson),
                author=CommentAuthor.SYSTEM,
                kind=KIND,
                run_id=run_id,
                data=redact_data(data),
            )
            result.comment_id = comment.id
            run = session.get(Run, run_id)
            if run is not None and result.cost_usd:
                run.cost_usd = (run.cost_usd or 0.0) + result.cost_usd
        return result
    except Exception as exc:  # a lesson must never fail a run
        log.exception("could not write the lesson for run %s", run_id)
        return LessonResult(error=f"{type(exc).__name__}: {exc}")


def redact_data(data: dict[str, Any]) -> dict[str, Any]:
    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        return value

    return {k: clean(v) for k, v in data.items()}


def latest(db: Database, card_id: int) -> tuple[Lesson, dict[str, Any]] | None:
    """The newest lesson on a card and its stored data."""
    with db.session() as session:
        comment = session.scalars(
            select(Comment)
            .where(Comment.card_id == card_id, Comment.kind == KIND)
            .order_by(Comment.id.desc())
            .limit(1)
        ).first()
        data = dict(comment.data or {}) if comment is not None else None
    if data is None:
        return None
    return Lesson.from_dict(data), data


def render(lesson: Lesson) -> str:
    """The lesson as Markdown, for the CLI and the comment body."""
    out = [f"# {lesson.title or 'Lesson'}", "", "## The idea", lesson.idea, ""]
    out += ["## What happened here", lesson.happened, ""]
    for excerpt in lesson.excerpts:
        out += [f"`{excerpt.file}`", "", "```", excerpt.code, "```", excerpt.note, ""]
    out += ["## Why this way", lesson.why, ""]
    if lesson.alternatives:
        out += ["## Other ways", *[f"- {a}" for a in lesson.alternatives], ""]
    if lesson.fits:
        out += ["## Where it fits", lesson.fits, ""]
    if lesson.trap:
        out += ["## The trap", lesson.trap, ""]
    if lesson.question:
        out += ["## A question", lesson.question, "", f"Answer: {lesson.answer}", ""]
    if not lesson.checked:
        out += ["(Unchecked: none of the code excerpts could be found in the diff.)", ""]
    return "\n".join(out).strip() + "\n"
