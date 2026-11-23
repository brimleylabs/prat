"""Screens in the engine: a card that changes interface code has its pages looked at when its
builder ends its turn, and only the faults it brought go back to it (prat.engine.screencheck)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import add_card, make_engine, project_toml
from prat import screens, store
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus
from prat.engine import screencheck
from prat.journal import read_journal

pytestmark = pytest.mark.integration

SCREENS = (
    '\n[screens]\nenvironment = "web"\npages = ["/"]\n\n'
    f'[[environment]]\nname = "web"\nstart = [{sys.executable!r}, "-c", "pass"]\n'
)
PAGE = {"op": "write", "path": "web/index.html", "content": "<button>Go</button>\n"}
CODE = {"op": "write", "path": "api.py", "content": "x = 1\n"}
OLD = screens.Fault("contrast", 390, "p.hint", "contrast is 2.8 to 1; it needs 4.5 to 1")
NEW = screens.Fault("covered", 390, "button#go", "sits under div.banner")


def looks(monkeypatch: pytest.MonkeyPatch, project: Project) -> list[Path]:
    """Replace the browser: the default branch has one old fault, a card's work adds one, and
    its second look finds it fixed."""
    seen: list[Path] = []
    clone = Path(project.agent_path).resolve()

    def look(manager: Any, slug: str, spec: Any, base: Path, *args: Any, **more: Any) -> Any:
        where = Path(base).resolve()
        seen.append(where)
        of_card = [p for p in seen if p != clone]
        found = [OLD, NEW] if where != clone and len(of_card) == 1 else [OLD]
        return screencheck.Look(looked=True, faults={"/": found}, seconds=1.0)

    monkeypatch.setattr(screencheck, "look", look)
    return seen


def journal_of(db: Database, card_id: int) -> tuple[Any, list[Any]]:
    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None and run.journal_path
        return run, list(read_journal(Path(run.journal_path)))


async def test_a_new_layout_fault_goes_back_to_the_builder_once(
    settings: Settings,
    db: Database,
    project_factory: Callable[..., Project],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = project_factory(project_toml=project_toml(extra=SCREENS))
    seen = looks(monkeypatch, project)
    fix = {"op": "write", "path": "web/index.html", "content": "<button>Go!</button>\n"}
    script = [PAGE, {"op": "finish"}, fix, {"op": "finish", "message": "moved it"}]
    engine, runtime = make_engine(settings, db, script, race_builders=1)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    await engine.checkouts_set_up()
    assert result.card_status is CardStatus.REVIEW, result.reason
    run, journal = journal_of(db, card_id)
    assert len(runtime.sessions) == 1
    looked = [e.data for e in journal if e.kind == "verify.screens"]
    # First look: two faults, one of them new. Second: the new one is gone.
    assert [(d["faults"], d["new"]) for d in looked] == [(2, 1), (1, 0)]
    clone = Path(project.agent_path).resolve()
    assert seen.count(clone) == 1  # the default branch is measured once
    baseline = [e.data for e in journal if e.kind == "verify.screens_baseline"]
    assert baseline and baseline[0]["faults"] == 1
    note = screencheck.read_note(settings.run_dir(run.id))
    assert "found no new layout fault" in note and "Design checklist" in note
    assert "The screens this card changed" in engine.reviewer.requests[-1].knowledge


async def test_cards_that_touch_no_interface_code_are_not_looked_at(
    settings: Settings,
    db: Database,
    project_factory: Callable[..., Project],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = project_factory(project_toml=project_toml(extra=SCREENS))
    seen = looks(monkeypatch, project)
    engine, _ = make_engine(settings, db, [CODE, {"op": "finish"}], race_builders=1)
    card_id = add_card(db, project)
    assert (await engine.run_card(card_id)).card_status is CardStatus.REVIEW
    assert seen == []
    assert not [e for e in journal_of(db, card_id)[1] if e.kind == "verify.screens"]


async def test_builders_of_interface_cards_get_the_standards(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(project_toml=project_toml(extra="\n[screens]\n"))
    engine, _ = make_engine(settings, db, [CODE, {"op": "finish"}], race_builders=1)
    ui = add_card(db, project, title="Add a filter button to the planner page sidebar")
    other = add_card(db, project, title="Speed up the nightly export job")
    await engine.run_card(ui)
    await engine.run_card(other)
    kinds = [
        [e.kind for e in journal_of(db, card)[1] if e.kind == "execute.design_standards"]
        for card in (ui, other)
    ]
    assert kinds == [["execute.design_standards"], []]
