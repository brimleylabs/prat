"""Racing builders (prat.engine.race): the first builder to finish with a change is kept."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import add_card, git, make_engine
from prat import store
from prat.config import Settings
from prat.db import Database, Project, Run
from prat.domain import CardStatus
from prat.runtime import AgentTask, FakeRuntime, FakeScript

pytestmark = pytest.mark.integration


def racing(lead: list[dict], rival: list[dict]) -> FakeRuntime:
    def script_for(task: AgentTask) -> FakeScript:
        return FakeScript.parse(rival if task.metadata.get("role") == "rival" else lead)

    return FakeRuntime(script_for)


def run_of(db: Database, card_id: int) -> Run:
    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None
        for step in run.steps:
            _ = step.detail
        return run


def execute_detail(run: Run) -> dict:
    return next(dict(s.detail or {}) for s in run.steps if str(s.name) == "execute")


def write(path: str, text: str, wait: float = 0) -> list[dict]:
    steps: list[dict] = [{"op": "wait", "seconds": wait}] if wait else []
    return [*steps, {"op": "write", "path": path, "content": text}, {"op": "finish"}]


async def run_race(
    settings: Settings, db: Database, project: Project, runtime: FakeRuntime
) -> tuple[Run, Callable[[str], str]]:
    engine, _ = make_engine(settings, db, runtime, race_builders=2)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW, result.reason
    run = run_of(db, card_id)
    agent = Path(project.agent_path)

    def on_branch(path: str) -> str:
        return git(agent, "show", f"{run.branch}:{path}")

    return run, on_branch


async def test_the_rival_finishes_first_and_the_card_takes_its_work(
    settings: Settings, db: Database, project: Project
) -> None:
    runtime = racing(lead=write("lead.txt", "lead", wait=30), rival=write("rival.txt", "rival"))
    run, on_branch = await run_race(settings, db, project, runtime)
    race = execute_detail(run)["race"]
    assert race["winner"] == "rival" and race["seconds"] < 20
    assert on_branch("rival.txt") == "rival"
    with pytest.raises(Exception):
        on_branch("lead.txt")
    # The rival's worktree and branch are gone.
    agent = Path(project.agent_path)
    assert not Path(str(run.worktree_path) + "-race").exists()
    assert "-race" not in git(agent, "branch", "--list")


async def test_the_lead_finishes_first_and_keeps_its_work(
    settings: Settings, db: Database, project: Project
) -> None:
    runtime = racing(lead=write("lead.txt", "lead"), rival=write("rival.txt", "rival", wait=30))
    run, on_branch = await run_race(settings, db, project, runtime)
    assert execute_detail(run)["race"]["winner"] == "lead"
    assert on_branch("lead.txt") == "lead"


async def test_a_builder_that_finishes_without_a_change_does_not_win(
    settings: Settings, db: Database, project: Project
) -> None:
    runtime = racing(lead=write("lead.txt", "lead", wait=1), rival=[{"op": "finish"}])
    run, on_branch = await run_race(settings, db, project, runtime)
    assert execute_detail(run)["race"]["winner"] == "lead"
    assert on_branch("lead.txt") == "lead"


async def test_without_racing_there_is_one_builder(
    settings: Settings, db: Database, project: Project
) -> None:
    seen: list[str] = []

    def script_for(task: AgentTask) -> FakeScript:
        seen.append(task.metadata.get("role", ""))
        return FakeScript.parse(write("lead.txt", "lead"))

    engine, _ = make_engine(settings, db, FakeRuntime(script_for))
    card_id = add_card(db, project)
    await engine.run_card(card_id)
    assert "rival" not in seen
    assert "race" not in execute_detail(run_of(db, card_id))
