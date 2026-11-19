"""Crews, memory, the governor, epics and jobs, through the real engine on the fake runtime."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import DEFAULT_TOML, PY, add_card, make_engine
from prat import jobs, store
from prat.config import Settings
from prat.db import Card, Database, Project, Run
from prat.domain import CardKind, CardStatus
from prat.memory import AgentMemory, all_agents

pytestmark = pytest.mark.integration

SCRIPT = [
    {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"},
    {"op": "finish", "message": "greet added"},
]


async def wait_for(predicate: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.05)


def status(db: Database, card_id: int) -> CardStatus:
    with db.session() as session:
        return store.get_card(session, card_id).status


def lead_memory(settings: Settings) -> AgentMemory:
    leads = [a for a in all_agents(settings.home) if a not in ("critic", "planner")]
    assert leads, "no crew member remembered the run"
    return AgentMemory(settings.home, leads[0])


async def test_the_lead_remembers_its_work_and_how_it_turned_out(
    settings: Settings, db: Database, project: Project
) -> None:
    engine, _ = make_engine(settings, db, SCRIPT)
    card_id = add_card(db, project, title="Add greet")
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW, result.reason
    memory = lead_memory(settings)
    (episode,) = [e for e in memory.episodes() if e.card_id == card_id]
    assert episode.title == "Add greet" and episode.outcome == "pending"
    # The reviewer remembers its verdict too.
    assert any(e.card_id == card_id for e in AgentMemory(settings.home, "critic").episodes())
    await engine.approve(card_id)
    (landed,) = [e for e in memory.episodes() if e.card_id == card_id]
    assert landed.outcome == "landed" and "proud" in landed.feeling.words
    with db.session() as session:
        run = session.get(Run, result.run_id)
        assert run is not None and run.live and run.live.get("lead")


async def test_a_card_sent_back_is_remembered_with_the_feedback(
    settings: Settings, db: Database, project: Project
) -> None:
    engine, _ = make_engine(settings, db, SCRIPT)
    card_id = add_card(db, project, title="Add greet")
    await engine.run_card(card_id)
    engine.rework(card_id, "greet should say hello, not hi")
    episode = next(e for e in lead_memory(settings).episodes() if e.card_id == card_id)
    assert episode.outcome == "reworked" and "hello" in episode.feedback
    assert episode.feeling.valence < 0


async def test_a_part_waits_until_the_part_it_needs_has_landed(
    settings: Settings, db: Database, project: Project
) -> None:
    engine, _ = make_engine(settings, db, SCRIPT, governor_poll_seconds=0.05)
    first = add_card(db, project, title="Schema")
    second = add_card(db, project, title="API")
    with db.session() as session:
        card = session.get(Card, second)
        assert card is not None
        card.depends_on = [first]
    waiting = engine.submit(second)

    def held() -> bool:
        with db.session() as session:
            card = session.get(Card, second)
            return bool(card and card.hold_reason and f"#{first}" in card.hold_reason)

    await wait_for(held)
    assert status(db, second) is CardStatus.READY
    await engine.run_card(first)
    await engine.approve(first)
    result = await asyncio.wait_for(waiting, timeout=30)
    # It started once #first landed (its script repeats #first's change, so it finds nothing
    # new to write; what matters is that it ran).
    assert result.run_id is not None
    with db.session() as session:
        card = session.get(Card, second)
        assert card is not None and card.hold_reason is None


async def test_an_epic_is_split_into_parts_that_run_once_approved(
    settings: Settings, db: Database, project: Project
) -> None:
    split = {
        "parts": [
            {
                "title": "Schema",
                "goal": "add table",
                "acceptance_criteria": "a\nb",
                "complexity": "low",
            },
            {
                "title": "API",
                "goal": "api/routes.py",
                "acceptance_criteria": "c",
                "depends_on": [0],
            },
            {"title": "Docs", "goal": "docs/", "acceptance_criteria": "d", "depends_on": [0, 1]},
        ]
    }
    engine, _ = make_engine(settings, db, [{"op": "finish", "message": json.dumps(split)}])
    with db.session() as session:
        epic = store.add_card(
            session,
            store.get_project(session, project.id),
            title="Trip sharing",
            kind=CardKind.EPIC,
            ready=True,
        )
        epic_id = epic.id
    result = await engine.run_card(epic_id)
    assert result.card_status is CardStatus.REVIEW, result.reason
    with db.session() as session:
        parts = list(session.query(Card).filter(Card.parent_id == epic_id).order_by(Card.id))
        ids = [p.id for p in parts]
        assert [p.status for p in parts] == [CardStatus.DRAFT] * 3
        assert parts[1].depends_on == [ids[0]] and parts[2].depends_on == ids[:2]
    engine2, _ = make_engine(settings, db, SCRIPT, governor_poll_seconds=0.05, swarm_epics=False)
    await engine2.approve(epic_id)
    assert status(db, epic_id) is CardStatus.DONE
    assert status(db, ids[0]) in (CardStatus.READY, CardStatus.RUNNING, CardStatus.REVIEW)
    for task in list(engine2._tasks.values()):
        task.cancel()


def test_a_job_reports_progress_and_its_end(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    script = (
        "from prat.progress import Progress\n"
        "p = Progress(total=3, phase='enrich', unit='rows', every_seconds=0)\n"
        "for name in 'abc':\n"
        "    p.advance(item=name)\n"
        "p.finish()\n"
    )
    toml = DEFAULT_TOML + f'\n[[job]]\nname = "enrich"\ncommand = ["{PY}", "job.py"]\n'
    project = project_factory(name="shop", project_toml=toml, files={"job.py": script})
    run_id = jobs.start(settings, db, "shop", "enrich")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = next(s for s in jobs.states(db) if s.run_id == run_id)
        if state.status != "running":
            break
        time.sleep(0.2)
    assert state.status == "succeeded", (state.reason, state.log_tail)
    assert state.progress.current is not None and state.progress.current.done == 3
    assert "enrich" in jobs.summary(state)
    assert Path(project.checkout_path).is_dir()
