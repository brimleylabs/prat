"""Epics: a feature too big for one card, split into parts.

An epic is a card of kind ``epic``. Its run does one thing: the planner reads the codebase and
proposes the parts (2 to 8 child cards, each with a goal, acceptance criteria, a complexity and
the parts it depends on). The epic then waits in review: the split is a plan, and plans are where
mistakes are cheapest. Approving it makes the parts ready; each runs, is reviewed and lands like
any card, parts with no dependency between them at the same time (the governor holds a part
until the parts it depends on have landed, and keeps colliding parts apart). When the last part
lands, the epic gets a summary of the whole feature.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from prat import store
from prat.db import Card, Comment, Database
from prat.domain import CardKind, CardStatus, CommentAuthor, Complexity

MIN_PARTS, MAX_PARTS = 2, 8

EPIC_PROMPT = """\
You split a feature into parts that separate agents build at the same time; each part is checked
and merged into the feature as soon as it passes. Read the codebase first (read-only tools) so
the parts follow how the code is really organised.

First decide whether to split at all. Every part has a cost of its own (an agent starting, its
checks, its review, merging), and parts that wait on each other run one after another. When one
agent would build the whole feature in a few minutes (a few hundred lines in a few files), or the
parts would mostly wait on each other, splitting is slower: answer {{"one_card": true}} and the
feature is built as one card. Split when the feature is big and its parts can truly run at once.

Rules for the parts:
- Between {lo} and {hi} parts, and no more than the feature needs: do not split work one agent
  does quickly anyway.
- Speed comes from parts running at once. Keep the dependency chain short: a part depends on
  another only when it cannot be written and tested without that part's code. Prefer one
  foundation part, then parts that need only the foundation.
- Where parts meet (function names and signatures, fields, module names), write the exact
  interface into the goal of every part that uses it, so each agent builds against the same
  contract.
- Each part must land on its own and leave the project working, and names the files it will
  mostly touch, in its goal. Parts that run at the same time do not edit the same file: give
  each its own module where the contract allows, since two parts changing one file collide when
  they merge and one of them is built again.
{brief}

Answer with JSON only, no prose before or after:
{{"parts": [{{"title": "...", "goal": "...", "acceptance_criteria": "line one\\nline two",
  "complexity": "low|medium|high", "depends_on": [0]}}]}}
where depends_on lists the indexes (0-based) of earlier parts this one needs.
"""

# How much a part's goal says. In full: the planner writes each part out, which for a long epic
# is most of the epic's text again, one word at a time, before any builder starts.
BRIEF_IN_FULL = (
    "- Acceptance criteria are testable statements, one per line; each part tests its own work."
)
# By reference (settings.epic_parts_by_reference): every part's builder is given the whole
# epic, and the planner only says which piece is whose.
BRIEF_BY_REFERENCE = """\
- Every part's builder is given the whole text of the epic, so never copy it. A part's goal is a
  few lines: which piece of the epic it builds (name the numbered section, the heading or the
  functions), the files it owns, and only what the epic does not already say, such as an
  interface two parts must agree on. Its acceptance criteria are one or two lines naming that
  piece; each part tests its own work."""


def split_prompt(*, by_reference: bool) -> str:
    return EPIC_PROMPT.format(
        lo=MIN_PARTS, hi=MAX_PARTS, brief=BRIEF_BY_REFERENCE if by_reference else BRIEF_IN_FULL
    )


def part_goal(part_goal_text: str, epic: Card, *, by_reference: bool) -> str:
    """The goal of a part's card. By reference it carries the whole epic under the part's own
    few lines, marked as reference, so the builder has every rule the epic states."""
    own = f"{part_goal_text}\n\nPart of epic #{epic.id}: {epic.title}."
    if not by_reference:
        return own
    criteria = (epic.acceptance_criteria or "").strip()
    return (
        f"{own} Other agents are building the other parts at this moment: build and test only "
        "the piece described above, and leave the rest of the epic to them. This card is "
        "reviewed against that piece and its own acceptance criteria alone.\n\n"
        "## The whole epic, for reference\n\n"
        f"{(epic.goal or '').strip()}"
        + (f"\n\nThe epic's acceptance criteria:\n{criteria}" if criteria else "")
    )


@dataclass
class Part:
    title: str
    goal: str
    acceptance_criteria: str
    complexity: Complexity = Complexity.MEDIUM
    depends_on: list[int] = field(default_factory=list)


class EpicError(ValueError):
    pass


def parse_parts(text: str) -> list[Part]:
    """The parts in a planner's answer: an empty list when the planner says the feature is
    built faster as one card. Raises EpicError when there is no usable answer."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if match is None:
        raise EpicError("the planner did not answer with JSON")
    try:
        data = json.loads(match.group(0))
    except ValueError as exc:
        raise EpicError(f"the planner's JSON did not parse: {exc}") from exc
    if isinstance(data, dict) and data.get("one_card") is True:
        return []
    raw = data.get("parts") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise EpicError("no parts in the planner's answer")
    parts = []
    for index, item in enumerate(raw[:MAX_PARTS]):
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        try:
            complexity = Complexity(str(item.get("complexity") or "medium").lower())
        except ValueError:
            complexity = Complexity.MEDIUM
        deps = [
            int(d)
            for d in item.get("depends_on") or []
            if isinstance(d, int | str) and str(d).isdigit() and int(d) < index
        ]
        criteria = item.get("acceptance_criteria") or ""
        if isinstance(criteria, list):
            criteria = "\n".join(str(c) for c in criteria)
        parts.append(
            Part(
                title=str(item["title"]).strip()[:200],
                goal=str(item.get("goal") or "").strip(),
                acceptance_criteria=str(criteria).strip(),
                complexity=complexity,
                depends_on=deps,
            )
        )
    if len(parts) < MIN_PARTS:
        raise EpicError(
            f"the planner proposed {len(parts)} parts; an epic needs at least {MIN_PARTS}"
        )
    return parts


def create_parts(
    db: Database, epic_id: int, parts: list[Part], *, by_reference: bool = False
) -> list[int]:
    """Add the parts as draft child cards of the epic. Returns their ids, in order."""
    ids: list[int] = []
    with db.session() as session:
        epic = store.get_card(session, epic_id)
        for old in session.scalars(select(Card).where(Card.parent_id == epic_id)):
            if old.status == CardStatus.DRAFT:
                store.set_card_status(session, old, CardStatus.CANCELLED, "replaced by a new split")
        for part in parts:
            card = store.add_card(
                session,
                epic.project,
                title=part.title,
                goal=part_goal(part.goal, epic, by_reference=by_reference),
                acceptance_criteria=part.acceptance_criteria,
                complexity=part.complexity,
                kind=CardKind.CHANGE,
            )
            card.parent_id = epic_id
            card.depends_on = [ids[d] for d in part.depends_on if d < len(ids)] or None
            ids.append(card.id)
    return ids


def approve_split(db: Database, epic_id: int) -> list[int]:
    """The person approved the split: the parts become ready, the epic is done. Returns the
    part ids that are now ready."""
    ready = []
    with db.session() as session:
        epic = store.get_card(session, epic_id)
        parts = list(session.scalars(select(Card).where(Card.parent_id == epic_id)))
        for part in parts:
            if part.status == CardStatus.DRAFT:
                store.set_card_status(session, part, CardStatus.READY, f"part of epic #{epic_id}")
                ready.append(part.id)
        store.set_card_status(
            session, epic, CardStatus.DONE, f"split into {len(parts)} parts; they run as cards"
        )
    return ready


@dataclass
class EpicView:
    id: int
    title: str
    project: str
    status: str
    parts: list[dict[str, Any]]

    @property
    def landed(self) -> int:
        return sum(1 for p in self.parts if p["status"] == str(CardStatus.DONE))

    @property
    def finished(self) -> bool:
        return bool(self.parts) and self.landed == len(self.parts)


def views(db: Database, project_ids: set[int] | None = None) -> list[EpicView]:
    """Every epic with its parts, newest first, open ones before finished ones."""
    found = []
    with db.session() as session:
        epics = list(
            session.scalars(select(Card).where(Card.kind == CardKind.EPIC).order_by(Card.id.desc()))
        )
        for epic in epics:
            if project_ids is not None and epic.project_id not in project_ids:
                continue
            parts = list(
                session.scalars(select(Card).where(Card.parent_id == epic.id).order_by(Card.id))
            )
            found.append(
                EpicView(
                    id=epic.id,
                    title=epic.title,
                    project=epic.project.slug,
                    status=str(epic.status),
                    parts=[
                        {
                            "id": p.id,
                            "title": p.title,
                            "status": str(p.status),
                            "depends_on": list(p.depends_on or []),
                            "hold": p.hold_reason,
                        }
                        for p in parts
                        if p.status != CardStatus.CANCELLED
                    ],
                )
            )
    found.sort(key=lambda v: (v.finished, -v.id))
    return found


def on_part_landed(db: Database, part_id: int) -> int | None:
    """When the last part of an epic lands, write the epic's summary. Returns the epic id."""
    with db.session() as session:
        part = session.get(Card, part_id)
        if part is None or part.parent_id is None:
            return None
        epic = session.get(Card, part.parent_id)
        if epic is None:
            return None
        parts = [
            p
            for p in session.scalars(
                select(Card).where(Card.parent_id == epic.id).order_by(Card.id)
            )
            if p.status != CardStatus.CANCELLED
        ]
        if not parts or any(p.status != CardStatus.DONE for p in parts):
            return None
        already = session.scalar(
            select(Comment).where(Comment.card_id == epic.id, Comment.kind == "epic_summary")
        )
        if already is not None:
            return None
        lines = [f"Every part of this epic has landed ({len(parts)} cards):", ""]
        for p in parts:
            summary = " ".join((p.summary or p.status_reason or "").split())[:300]
            lines.append(f"- #{p.id} {p.title}" + (f": {summary}" if summary else ""))
        store.add_comment(
            session, epic, "\n".join(lines), author=CommentAuthor.SYSTEM, kind="epic_summary"
        )
        return epic.id
