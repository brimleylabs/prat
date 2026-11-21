"""Fact packs in the engine: a waiting card is researched and checked, and the builder (and
only the builder) gets what still holds when the card is built (prat.packs)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import WRITE_GREET, add_card, git, make_engine
from prat import packs, store
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus, Complexity
from prat.journal import read_journal
from prat.runtime import AgentTask, FakeRuntime, FakeScript

pytestmark = pytest.mark.integration

DRAFT = "## Files to touch\n- `README.md:1` is the only file here.\n- Wrong: greet takes a name."
CHECKED = "## Files to touch\n- `README.md:1` is the only file here."
NOTES = "\n\n## Verification notes\n- Removed: greet takes a name. It takes no arguments."


def crew(seen: list[AgentTask]) -> FakeRuntime:
    """A researcher that writes a pack, a checker that corrects it, and a builder."""

    def script_for(task: AgentTask) -> FakeScript:
        seen.append(task)
        if task.metadata.get("role") == "researcher":
            checking = "# The pack to check" in task.prompt
            return FakeScript.parse(
                [{"op": "finish", "message": CHECKED + NOTES if checking else DRAFT}]
            )
        return FakeScript.parse(WRITE_GREET)

    return FakeRuntime(script_for)


def waiting_card(db: Database, project: Project, **values: Any) -> int:
    values = {"goal": "Add a greet function.", "complexity": Complexity.MEDIUM, **values}
    return add_card(db, project, ready=False, **values)


def fact_pack_events(db: Database, card_id: int) -> list[dict[str, Any]]:
    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None and run.journal_path
        journal = list(read_journal(Path(run.journal_path)))
    return [e.data for e in journal if e.kind == "execute.fact_pack"]


async def test_a_waiting_card_is_researched_checked_and_built_with_its_pack(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory()
    seen: list[AgentTask] = []
    engine, _ = make_engine(settings, db, crew(seen), fact_packs="backlog")
    card_id = waiting_card(db, project)

    assert engine.prepare_packs() == card_id
    assert engine.prepare_packs() is None  # one card at a time
    await engine.packs_prepared()
    pack = packs.load(settings.home, card_id)
    assert pack is not None and pack.usable and pack.text == CHECKED and pack.corrections == 1
    researcher, checker = seen
    # Both read a checkout of the default branch of their own, and cannot write.
    assert researcher.workdir.name == f"pack-{card_id}" and not researcher.workdir.exists()
    assert not {"Write", "Edit", "Bash"} & {str(t) for t in checker.allowed_tools}
    assert engine.prepare_packs() is None  # researched: nothing more to do for this text

    with db.session() as session:
        store.set_card_status(session, store.get_card(session, card_id), CardStatus.READY)
    result = await engine.run_card(card_id)
    await engine.checkouts_set_up()
    assert result.card_status is CardStatus.REVIEW, result.reason
    builder = seen[-1]
    assert packs.HEADING in builder.system_prompt and CHECKED in builder.system_prompt
    assert "Wrong: greet takes a name" not in builder.system_prompt
    assert fact_pack_events(db, card_id) == [
        {"used": True, "kept": 1, "dropped": 0, "reason": "", "researched_at": pack.commit}
    ]


async def test_a_pack_is_not_used_once_the_card_or_the_code_moved_on(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory()
    seen: list[AgentTask] = []
    engine, _ = make_engine(settings, db, crew(seen), fact_packs="backlog")
    changed_card = waiting_card(db, project)
    changed_code = waiting_card(db, project, title="Add farewell", goal="Add a farewell function.")
    for card_id in (changed_card, changed_code):
        await engine.prepare_pack(card_id)

    # The first card is rewritten; the file the second card's pack is about changes.
    with db.session() as session:
        store.get_card(session, changed_card).goal = "Add a greet function that takes a name."
    (Path(project.checkout_path) / "README.md").write_text("rewritten\n", encoding="utf-8")
    git(Path(project.checkout_path), "commit", "--quiet", "-am", "Rewrite the readme")

    for card_id, reason in (
        (changed_card, "the card changed since it was researched"),
        (changed_code, "too much of the code changed"),
    ):
        with db.session() as session:
            store.set_card_status(session, store.get_card(session, card_id), CardStatus.READY)
        result = await engine.run_card(card_id)
        await engine.checkouts_set_up()
        assert result.card_status is CardStatus.REVIEW, result.reason
        assert packs.HEADING not in seen[-1].system_prompt
        (event,) = fact_pack_events(db, card_id)
        assert event["used"] is False and event["reason"] == reason


async def test_low_complexity_cards_and_a_busy_engine_are_left_alone(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory()
    engine, _ = make_engine(settings, db, crew([]), fact_packs="backlog")
    waiting_card(db, project, complexity=Complexity.LOW)
    assert engine.prepare_packs() is None
    off, _ = make_engine(settings, db, crew([]), fact_packs="off")
    waiting_card(db, project)
    assert off.prepare_packs() is None
    # While a card is being built, research waits.
    slow = [{"op": "wait", "seconds": 1.0}, *WRITE_GREET]
    busy, _ = make_engine(settings, db, slow, fact_packs="backlog")
    building = add_card(db, project, title="Being built", complexity=Complexity.LOW)
    task = busy.submit(building)
    await asyncio.sleep(0.2)
    assert busy.prepare_packs() is None
    await task
    await busy.checkouts_set_up()
    assert busy.prepare_packs() is not None
    await busy.packs_prepared()
