"""Swarm epics (prat.swarm): parts built at once on the epic's branch, merged in as they pass,
the whole feature reviewed once."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import git, make_engine
from prat import store
from prat.config import Settings
from prat.db import Card, Database, Project
from prat.domain import CardKind, CardStatus
from prat.engine import Engine
from prat.runtime import AgentTask, FakeRuntime, FakeScript

pytestmark = pytest.mark.integration


async def wait_for(predicate: Callable[[], bool], timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.05)


def status(db: Database, card_id: int) -> CardStatus:
    with db.session() as session:
        return store.get_card(session, card_id).status


def parts_of(db: Database, epic_id: int) -> list[Card]:
    with db.session() as session:
        found = list(session.query(Card).filter(Card.parent_id == epic_id).order_by(Card.id).all())
        session.expunge_all()
    return found


def add_epic(db: Database, project: Project, title: str = "Trip sharing") -> int:
    with db.session() as session:
        epic = store.add_card(
            session,
            store.get_project(session, project.id),
            title=title,
            kind=CardKind.EPIC,
            ready=True,
        )
        return epic.id


def runtime_for(split: dict, builder: Callable[[AgentTask], list[dict]]) -> FakeRuntime:
    """The planner answers with ``split``; each part's builder runs ``builder(task)``."""

    def script_for(task: AgentTask) -> FakeScript:
        if task.metadata.get("role") == "planner" and "Split this epic" in task.prompt:
            return FakeScript.parse([{"op": "finish", "message": json.dumps(split)}])
        return FakeScript.parse(builder(task))

    return FakeRuntime(script_for)


def title_in(task: AgentTask, *titles: str) -> str:
    return next(t for t in titles if f"# {t}" in task.system_prompt or t in task.system_prompt)


async def build_epic(engine: Engine, db: Database, epic_id: int) -> None:
    result = await engine.run_card(epic_id)
    assert result.card_status is CardStatus.REVIEW, result.reason
    await engine.approve(epic_id)
    assert status(db, epic_id) is CardStatus.BUILDING
    await wait_for(lambda: status(db, epic_id) is not CardStatus.BUILDING)


async def test_a_swarm_builds_the_parts_on_the_epics_branch_and_the_feature_lands_once(
    settings: Settings, db: Database, project: Project
) -> None:
    split = {
        "parts": [
            {"title": "Schema", "goal": "db/schema.sql", "acceptance_criteria": "a"},
            {"title": "Docs", "goal": "docs/sharing.md", "acceptance_criteria": "b"},
            {"title": "API", "goal": "api/share.py", "acceptance_criteria": "c", "depends_on": [0]},
        ]
    }
    seen: dict[str, list[str]] = {}

    def builder(task: AgentTask) -> list[dict]:
        name = title_in(task, "Schema", "Docs", "API")
        # What the part's worktree already holds when it starts.
        seen[name] = sorted(p.name for p in task.workdir.rglob("*.*") if ".git" not in p.parts)
        path = {"Schema": "db/schema.sql", "Docs": "docs/sharing.md", "API": "api/share.py"}
        return [
            {"op": "write", "path": path[name], "content": f"{name}\n"},
            {"op": "finish", "message": f"{name} done"},
        ]

    engine, _ = make_engine(settings, db, runtime_for(split, builder), governor_poll_seconds=0.05)
    epic_id = add_epic(db, project)
    await build_epic(engine, db, epic_id)

    assert status(db, epic_id) is CardStatus.REVIEW
    parts = parts_of(db, epic_id)
    assert [p.status for p in parts] == [CardStatus.DONE] * 3
    with db.session() as session:
        epic = store.get_card(session, epic_id)
        branch = epic.branch or ""
        summary = next(c.body for c in epic.comments if c.kind == "epic_summary")
    assert branch.startswith("epic/")
    assert "gates passed" in summary
    agent = Path(project.agent_path)
    # Every part is on the epic's branch, none on the default branch yet.
    for path in ("db/schema.sql", "docs/sharing.md", "api/share.py"):
        assert git(agent, "cat-file", "-t", f"{branch}:{path}") == "blob"
    assert "db/schema.sql" not in git(agent, "ls-tree", "-r", "--name-only", project.default_branch)
    # The API part waited for the schema and started from the epic's branch with it.
    assert "schema.sql" in seen["API"]

    merged = await engine.approve(epic_id)
    assert status(db, epic_id) is CardStatus.DONE
    tree = git(agent, "ls-tree", "-r", "--name-only", merged.commit)
    assert {"db/schema.sql", "docs/sharing.md", "api/share.py"} <= set(tree.splitlines())


async def test_a_part_that_conflicts_with_the_epic_is_sent_back_and_resolves_it(
    settings: Settings, db: Database, project: Project
) -> None:
    # Two independent parts change the same line of the same file.
    split = {
        "parts": [
            {"title": "Red", "goal": "colour.txt", "acceptance_criteria": "a"},
            {"title": "Blue", "goal": "colour.txt", "acceptance_criteria": "b"},
        ]
    }
    runs: list[str] = []

    def builder(task: AgentTask) -> list[dict]:
        name = title_in(task, "Red", "Blue")
        runs.append(name)
        conflicted = "conflict markers" in task.prompt or "conflict markers" in task.system_prompt
        content = "red\nblue\n" if conflicted else f"{name.lower()}\n"
        return [
            {"op": "write", "path": "colour.txt", "content": content},
            {"op": "finish", "message": f"{name} done"},
        ]

    # One at a time, so the second always meets the first's merged change.
    engine, _ = make_engine(
        settings,
        db,
        runtime_for(split, builder),
        governor_poll_seconds=0.05,
        max_concurrent_per_project=1,
    )
    epic_id = add_epic(db, project, title="Colours")
    await build_epic(engine, db, epic_id)

    assert status(db, epic_id) is CardStatus.REVIEW
    parts = parts_of(db, epic_id)
    assert [p.status for p in parts] == [CardStatus.DONE] * 2
    sent_back = [p for p in parts if any(c.kind == "swarm_conflict" for c in _comments(db, p.id))]
    assert len(sent_back) == 1
    assert len(runs) == 3
    with db.session() as session:
        branch = store.get_card(session, epic_id).branch or ""
    assert git(Path(project.agent_path), "show", f"{branch}:colour.txt") == "red\nblue"


async def test_without_swarms_each_part_comes_to_review_on_its_own(
    settings: Settings, db: Database, project: Project
) -> None:
    split = {
        "parts": [
            {"title": "One", "goal": "one.txt", "acceptance_criteria": "a"},
            {"title": "Two", "goal": "two.txt", "acceptance_criteria": "b"},
        ]
    }

    def builder(task: AgentTask) -> list[dict]:
        name = title_in(task, "One", "Two")
        return [
            {"op": "write", "path": f"{name.lower()}.txt", "content": "x\n"},
            {"op": "finish"},
        ]

    engine, _ = make_engine(
        settings, db, runtime_for(split, builder), governor_poll_seconds=0.05, swarm_epics=False
    )
    epic_id = add_epic(db, project, title="Numbers")
    await engine.run_card(epic_id)
    await engine.approve(epic_id)
    assert status(db, epic_id) is CardStatus.DONE
    ids = [p.id for p in parts_of(db, epic_id)]
    await wait_for(lambda: all(status(db, i) is CardStatus.REVIEW for i in ids))


def _comments(db: Database, card_id: int) -> list:
    with db.session() as session:
        found = list(store.get_card(session, card_id).comments)
        session.expunge_all()
    return found
