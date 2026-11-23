"""From a report to work: cards for what was found, started only as far as a person allows.

Findings are grouped by rule, one card per kind of fault, so a builder fixes "every image with
no alt text" and not forty cards of one image each. The cards go into the backlog as drafts
under one epic per evaluation ("Security remediation"), worst first. Nothing starts by itself.

A person starts what they choose, or opens a window: so many cards, for so many hours, at or
above a severity. While the window is open the server makes the next card ready as the one
before it finishes (:func:`advance`), a few at a time, and stops at whichever limit comes
first. What is left stays in the backlog for another day.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from prat import store
from prat.db import Card, Database
from prat.domain import CardKind, CardStatus, Complexity
from prat.evaluate import model
from prat.evaluate.library import TITLES
from prat.evaluate.model import Finding, Report

log = logging.getLogger(__name__)

CARDS_FILE = "cards.json"
WINDOW_FILE = "window.json"
LISTED = 40
# Cards of one window at work at the same time.
AT_ONCE = 2
OPEN = (
    CardStatus.DRAFT,
    CardStatus.READY,
    CardStatus.RUNNING,
    CardStatus.REVIEW,
    CardStatus.REWORK,
    CardStatus.BLOCKED,
)
BUSY = (CardStatus.READY, CardStatus.RUNNING, CardStatus.REWORK)


@dataclass
class Work:
    """One card's worth: every finding of one rule of one evaluation."""

    evaluation: str
    rule: str
    severity: str
    title: str
    findings: list[Finding]

    @property
    def key(self) -> str:
        return f"{self.evaluation}|{self.rule}"


def plan(
    report: Report,
    *,
    floor: str = "high",
    evaluations: Sequence[str] | None = None,
    limit: int | None = None,
    skip: Sequence[str] = (),
) -> list[Work]:
    """The cards a report calls for, worst first: rules with a finding at or above ``floor``,
    leaving out the ones that already have an open card (``skip``, their keys)."""
    wanted = [f for f in report.findings if evaluations is None or f.evaluation in evaluations]
    works = []
    for group in model.by_rule(wanted):
        worst = min((f.severity for f in group), key=model.rank)
        work = Work(group[0].evaluation, group[0].rule, worst, group[0].title, group)
        if model.at_least(worst, floor) and work.key not in skip:
            works.append(work)
    return works[:limit] if limit is not None else works


def card_text(slug: str, work: Work) -> tuple[str, str, str]:
    """A card's title, goal and acceptance criteria for one kind of fault."""
    count = len(work.findings)
    head = work.findings[0]
    places = "place" if count == 1 else "places"
    title = f"Fix: {work.title} ({count} {places})"[:200]
    lines = [
        f"A {TITLES.get(work.evaluation, work.evaluation).lower()} evaluation of this project "
        f"found this in {count} {places}. Severity: {work.severity}.",
    ]
    if head.standard:
        lines.append(f"Rule: {head.standard}.")
    lines += ["", "Where:"]
    for finding in work.findings[:LISTED]:
        detail = f": {finding.detail}" if finding.detail else ""
        lines.append(f"- {finding.where}{detail}")
    if count > LISTED:
        lines.append(f"- and {count - LISTED} more of the same kind; find them the same way")
    fixes = list(dict.fromkeys(f.fix for f in work.findings if f.fix))
    if fixes:
        lines += ["", "How to fix it:", *[f"- {fix}" for fix in fixes[:5]]]
    lines += [
        "",
        "Fix the cause where there is one (a shared component, a template, a version in one "
        "file) rather than each place by hand. Change nothing else.",
    ]
    acceptance = "\n".join(
        [
            f"- `prat evaluate run {work.evaluation} --project {slug}` no longer reports "
            f"`{work.rule}` in the places listed.",
            "- The project's own tests still pass.",
            "- Nothing behaves differently apart from the fix.",
        ]
    )
    return title, "\n".join(lines), acceptance


def _state(home: Path, slug: str) -> dict[str, Any]:
    try:
        data = json.loads((model.folder(home, slug) / CARDS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cards": {}, "epics": {}}
    return {"cards": dict(data.get("cards") or {}), "epics": dict(data.get("epics") or {})}


def _save_state(home: Path, slug: str, state: dict[str, Any]) -> None:
    path = model.folder(home, slug) / CARDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1), encoding="utf-8")


def open_keys(db: Database, home: Path, slug: str) -> list[str]:
    """Rules that already have a card which is not finished or given up."""
    state = _state(home, slug)
    with db.session() as session:
        return [
            key
            for key, card_id in state["cards"].items()
            if (card := session.get(Card, int(card_id))) is not None and card.status in OPEN
        ]


@dataclass
class Created:
    epics: dict[str, int] = field(default_factory=dict)
    cards: list[int] = field(default_factory=list)


def create(db: Database, home: Path, slug: str, works: Sequence[Work], report: Report) -> Created:
    """Add the cards as drafts under one epic per evaluation, in the order given. An
    evaluation's open epic from an earlier report is added to rather than repeated."""
    state = _state(home, slug)
    out = Created()
    with db.session() as session:
        project = store.get_project(session, slug)
        for work in works:
            epic_id = out.epics.get(work.evaluation)
            if epic_id is None:
                known = session.get(Card, int(state["epics"].get(work.evaluation) or 0))
                if known is not None and known.status in OPEN:
                    epic_id = known.id
                else:
                    name = TITLES.get(work.evaluation, work.evaluation)
                    epic = store.add_card(
                        session,
                        project,
                        title=f"{name} remediation",
                        goal=f"What {name.lower()} evaluations of this project found, one card "
                        "per kind of fault. This card only holds them together: its parts are "
                        "the work, and they are started one by one or through a remediation "
                        f"window (prat evaluate remediate). First report: {report.id}.",
                        kind=CardKind.EPIC,
                    )
                    epic_id = epic.id
                out.epics[work.evaluation] = epic_id
                state["epics"][work.evaluation] = epic_id
            title, goal, acceptance = card_text(slug, work)
            small = work.rule.startswith(("dependency-", "header-", "cookie-"))
            card = store.add_card(
                session,
                project,
                title=title,
                goal=goal,
                acceptance_criteria=acceptance,
                complexity=Complexity.LOW if small else Complexity.MEDIUM,
            )
            card.parent_id = epic_id
            out.cards.append(card.id)
            state["cards"][work.key] = card.id
    _save_state(home, slug, state)
    return out


def start(db: Database, card_ids: Sequence[int], reason: str) -> list[int]:
    """Make draft cards ready. Returns the ones that were."""
    started = []
    with db.session() as session:
        for card_id in card_ids:
            card = session.get(Card, card_id)
            if card is not None and card.status is CardStatus.DRAFT:
                store.set_card_status(session, card, CardStatus.READY, reason)
                started.append(card_id)
    return started


# A window: how much may be fixed without asking again ----------------------------------------


@dataclass
class Window:
    until: float
    max_cards: int
    epics: list[int]
    started: list[int] = field(default_factory=list)

    def open(self, now: float) -> bool:
        return now < self.until and len(self.started) < self.max_cards


def window_path(home: Path, slug: str) -> Path:
    return model.folder(home, slug) / WINDOW_FILE


def load_window(home: Path, slug: str) -> Window | None:
    try:
        data = json.loads(window_path(home, slug).read_text(encoding="utf-8"))
        return Window(
            float(data["until"]),
            int(data["max_cards"]),
            [int(e) for e in data["epics"]],
            [int(c) for c in data.get("started") or []],
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_window(home: Path, slug: str, window: Window) -> None:
    path = window_path(home, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(window), indent=1), encoding="utf-8")


def open_window(
    home: Path, slug: str, epics: Sequence[int], *, hours: float, max_cards: int
) -> Window:
    window = Window(time.time() + hours * 3600, max_cards, list(epics))
    save_window(home, slug, window)
    return window


def close_window(home: Path, slug: str) -> None:
    with contextlib.suppress(OSError):
        window_path(home, slug).unlink()


def advance(
    db: Database, home: Path, slug: str, *, now: float | None = None, at_once: int = AT_ONCE
) -> list[int]:
    """Make the next cards of an open window ready: the oldest drafts of its epics, while
    fewer than ``at_once`` of the window's cards are at work. A window past its time or its
    count is closed; cards already started finish."""
    window = load_window(home, slug)
    if window is None:
        return []
    now = time.time() if now is None else now
    if not window.open(now):
        close_window(home, slug)
        return []
    with db.session() as session:
        mine = [session.get(Card, card_id) for card_id in window.started]
        busy = sum(1 for card in mine if card is not None and card.status in BUSY)
        room = min(at_once - busy, window.max_cards - len(window.started))
        if room <= 0:
            return []
        drafts = session.scalars(
            select(Card)
            .where(Card.parent_id.in_(window.epics), Card.status == CardStatus.DRAFT)
            .order_by(Card.id)
            .limit(room)
        )
        ready = []
        for card in drafts:
            store.set_card_status(session, card, CardStatus.READY, "remediation window")
            ready.append(card.id)
    if not ready and not busy:
        close_window(home, slug)  # nothing left to do
        return []
    window.started += ready
    save_window(home, slug, window)
    return ready


async def watch(home: Path, db: Database, stop: asyncio.Event, every: float = 60.0) -> None:
    """The server's loop: keep every project's open window moving."""
    while not stop.is_set():
        try:
            base = home / model.DIR
            slugs = [p.parent.name for p in base.glob(f"*/{WINDOW_FILE}")] if base.is_dir() else []
            for slug in slugs:
                ready = await asyncio.to_thread(advance, db, home, slug)
                if ready:
                    log.info("remediation window for %s: cards %s are ready", slug, ready)
        except Exception:  # the loop must outlive one bad file or one bad query
            log.exception("remediation windows could not be advanced")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=every)
