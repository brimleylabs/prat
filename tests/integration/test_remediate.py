"""From a report to cards, and the window in which they may be worked through
(prat.evaluate.remediate)."""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from sqlalchemy import select

from prat import store
from prat.config import Settings
from prat.db import Card, Database, Project
from prat.domain import CardKind, CardStatus, Complexity
from prat.evaluate import model, remediate
from prat.evaluate.model import Finding, Report

pytestmark = pytest.mark.integration


def report() -> Report:
    found = [
        Finding("security", "secret-aws", "critical", "Committed secret", "settings.py:3"),
        Finding("security", "dependency-lodash", "high", "lodash has a known vulnerability",
                "package-lock.json", "<4.17.21", "Upgrade to lodash 4.17.21."),
        Finding("security", "code-B602", "high", "shell equals true", "app/run.py:12"),
        Finding("security", "code-B602", "medium", "shell equals true", "app/jobs.py:40"),
        Finding("security", "header-nosniff", "low", "No nosniff header", "https://shop.example/"),
        Finding("accessibility", "image-alt", "high", "Images must have alternative text",
                "/ img.hero", "no alt", "https://d.example/image-alt", "WCAG 2.2 1.1.1"),
    ]  # fmt: skip
    return Report(
        "shop", ["security", "accessibility"], "repo", id="20261123-090000", findings=found
    )


def statuses(db: Database, ids: list[int]) -> list[str]:
    with db.session() as session:
        return [str(session.get_one(Card, card_id).status) for card_id in ids]


def test_a_report_becomes_one_card_per_kind_of_fault_worst_first() -> None:
    works = remediate.plan(report())
    assert [(w.evaluation, w.rule, w.severity, len(w.findings)) for w in works] == [
        ("security", "secret-aws", "critical", 1),
        ("security", "code-B602", "high", 2),  # one card for both places
        ("security", "dependency-lodash", "high", 1),
        ("accessibility", "image-alt", "high", 1),
    ]
    assert len(remediate.plan(report(), floor="low")) == 5
    assert [w.rule for w in remediate.plan(report(), evaluations=["accessibility"])] == [
        "image-alt"
    ]
    assert len(remediate.plan(report(), limit=2, skip=["security|secret-aws"])) == 2
    title, goal, acceptance = remediate.card_text("shop", works[1])
    assert title == "Fix: shell equals true (2 places)"
    assert "- app/run.py:12" in goal and "- app/jobs.py:40" in goal and "Severity: high" in goal
    assert "`prat evaluate run security --project shop` no longer reports `code-B602`" in acceptance
    assert "Upgrade to lodash 4.17.21." in remediate.card_text("shop", works[2])[1]
    assert "Rule: WCAG 2.2 1.1.1." in remediate.card_text("shop", works[3])[1]


def test_cards_wait_in_the_backlog_under_an_epic_and_nothing_starts(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory()
    slug = project.slug
    made = remediate.create(db, settings.home, slug, remediate.plan(report()), report())
    assert set(made.epics) == {"security", "accessibility"} and len(made.cards) == 4
    with db.session() as session:
        epic = session.get_one(Card, made.epics["security"])
        assert (epic.kind, epic.title, epic.status) == (
            CardKind.EPIC,
            "Security remediation",
            CardStatus.DRAFT,
        )
        parts = list(
            session.scalars(select(Card).where(Card.parent_id == epic.id).order_by(Card.id))
        )
        assert [p.title for p in parts] == [
            "Fix: Committed secret (1 place)",
            "Fix: shell equals true (2 places)",
            "Fix: lodash has a known vulnerability (1 place)",
        ]
        assert {p.status for p in parts} == {CardStatus.DRAFT}
        assert parts[2].complexity is Complexity.LOW and parts[1].complexity is Complexity.MEDIUM
    # A second report does not repeat a card that is still open, and adds to the same epic.
    assert len(remediate.open_keys(db, settings.home, slug)) == 4
    later = remediate.plan(report(), floor="low", skip=remediate.open_keys(db, settings.home, slug))
    assert [w.rule for w in later] == ["header-nosniff"]
    again = remediate.create(db, settings.home, slug, later, report())
    assert again.epics == {"security": made.epics["security"]}
    # Started by hand: only what was named.
    assert remediate.start(db, made.cards[:1], "asked for") == made.cards[:1]
    assert statuses(db, made.cards) == ["ready", "draft", "draft", "draft"]


def test_a_window_works_through_cards_until_its_count_or_its_time_runs_out(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory()
    slug, home = project.slug, settings.home
    made = remediate.create(db, home, slug, remediate.plan(report()), report())
    assert remediate.advance(db, home, slug) == []  # no window: nothing moves
    remediate.open_window(home, slug, list(made.epics.values()), hours=2, max_cards=3)
    first = remediate.advance(db, home, slug)
    assert len(first) == remediate.AT_ONCE and statuses(db, first) == ["ready", "ready"]
    assert remediate.advance(db, home, slug) == []  # both still at work

    def finish(card_id: int) -> None:
        with db.session() as session:
            card = store.get_card(session, card_id)
            for status in (CardStatus.RUNNING, CardStatus.REVIEW, CardStatus.DONE):
                store.set_card_status(session, card, status, "test")

    finish(first[0])
    third = remediate.advance(db, home, slug)
    assert len(third) == 1  # the count allows one more, not two
    finish(first[1])
    finish(third[0])
    assert remediate.advance(db, home, slug) == []
    assert remediate.load_window(home, slug) is None  # used up, so closed
    assert statuses(db, made.cards).count("draft") == 1  # the rest stays in the backlog

    # A window past its time starts nothing more.
    remediate.open_window(home, slug, list(made.epics.values()), hours=1, max_cards=5)
    assert remediate.advance(db, home, slug, now=time.time() + 2 * 3600) == []
    assert remediate.load_window(home, slug) is None
    assert model.folder(home, slug).is_dir()
