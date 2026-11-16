"""Ideas: agents propose ways of working better, the person decides, an accepted idea is put
in place by a card, and the author remembers each step."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from conftest import add_card
from prat import ideas, store
from prat.db import Database, Project
from prat.engine.agent_tools import ToolContext, build
from prat.memory import AgentMemory, new_episode, track_record
from prat.runtime.fake import FakeRuntime

GOOD = {
    "title": "Run the gates from a clean checkout first",
    "problem": "Three setup cards were sent back because the gates passed only in a warm session.",
    "proposal": "Add a skill that runs git clean -ffdx and the gates before reporting done.",
    "benefit": "Setup cards land on the first review.",
}


def test_an_idea_is_proposed_once_and_waits_for_the_person(tmp_path: Path) -> None:
    first = ideas.propose(tmp_path, "python", **GOOD, kind="skill")
    again = ideas.propose(tmp_path, "python", **GOOD, kind="skill")
    assert again.id == first.id
    assert [i.id for i in ideas.waiting(tmp_path)] == [first.id]
    with pytest.raises(ideas.IdeaError):
        ideas.propose(tmp_path, "python", title="Thin", problem="x", proposal="y")


def test_an_agent_with_too_many_ideas_waiting_is_heard_first(tmp_path: Path) -> None:
    for n in range(ideas.MAX_WAITING_PER_AGENT):
        ideas.propose(tmp_path, "python", **{**GOOD, "title": f"{GOOD['title']} {n}"})
    with pytest.raises(ideas.IdeaError, match="waiting"):
        ideas.propose(tmp_path, "python", **{**GOOD, "title": "One more idea please"})


def test_the_decision_and_note_become_the_authors_memory(tmp_path: Path) -> None:
    accepted = ideas.propose(tmp_path, "python", **GOOD)
    declined = ideas.propose(
        tmp_path, "python", **{**GOOD, "title": "Skip the reviewer on small cards"}
    )
    ideas.decide(tmp_path, accepted.id, accept=True, note="Yes, good catch.")
    with pytest.raises(ideas.IdeaError, match="why"):
        ideas.decide(tmp_path, declined.id, accept=False)
    ideas.decide(tmp_path, declined.id, accept=False, note="Every card gets a review.")
    with pytest.raises(ideas.IdeaError, match="already"):
        ideas.decide(tmp_path, declined.id, accept=True)
    episodes = {e.outcome: e for e in AgentMemory(tmp_path, "python").episodes()}
    assert episodes["accepted"].feeling.words == "encouraged"
    assert episodes["declined"].feedback == "Every card gets a review."
    # Ideas are not cards: the agent's record of work is unchanged.
    assert track_record(AgentMemory(tmp_path, "python")) == ""


def test_an_accepted_idea_is_put_in_place_by_a_card(
    db: Database, project: Project, tmp_path: Path
) -> None:
    idea = ideas.propose(tmp_path, "python", **GOOD, kind="project", project=project.slug)
    with pytest.raises(ideas.IdeaError, match="accept"):
        ideas.put_in_place(db, tmp_path, idea.id)
    ideas.decide(tmp_path, idea.id, accept=True, note="Only for setup cards.")
    card_id = ideas.put_in_place(db, tmp_path, idea.id, ready=False)
    with db.session() as session:
        card = store.get_card(session, card_id)
        assert card.title.startswith("Put in place: Run the gates")
        assert "Only for setup cards." in card.goal and GOOD["proposal"] in card.goal
    with pytest.raises(ideas.IdeaError, match="already"):
        ideas.put_in_place(db, tmp_path, idea.id)
    assert ideas.card_landed(tmp_path, add_card(db, project, "unrelated")) is None
    landed = ideas.card_landed(tmp_path, card_id)
    assert landed is not None and ideas.get(tmp_path, idea.id).status == "in_place"
    proud = [e for e in AgentMemory(tmp_path, "python").episodes() if e.outcome == "in_place"]
    assert proud and proud[0].feeling.words == "proud"


def test_an_idea_about_no_project_needs_one_named_to_be_put_in_place(
    db: Database, project: Project, tmp_path: Path
) -> None:
    idea = ideas.propose(tmp_path, "critic", **GOOD, kind="process")
    ideas.decide(tmp_path, idea.id, accept=True)
    with pytest.raises(ideas.IdeaError, match="which project"):
        ideas.put_in_place(db, tmp_path, idea.id)
    card_id = ideas.put_in_place(db, tmp_path, idea.id, project=project.slug, ready=False)
    assert ideas.get(tmp_path, idea.id).work_card == card_id


def test_the_agent_tool_proposes_from_the_card(tmp_path: Path) -> None:
    ctx = ToolContext(
        home=tmp_path,
        project="shop",
        card_id=7,
        run_id=3,
        agent="python",
        record=lambda *_: None,
        live=lambda *_: None,
    )
    tool = next(t for t in build(ctx) if t.name == "suggest_improvement")
    answer = asyncio.run(tool.handler({**GOOD, "kind": "project"}))
    assert "with the person" in answer
    idea = ideas.waiting(tmp_path)[0]
    assert (idea.project, idea.card_id, idea.source) == ("shop", 7, "work")
    assert "needs" in asyncio.run(tool.handler({"title": "x"}))


def test_a_dream_lets_agents_with_new_experience_propose(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path, "python")
    for n in range(3):
        memory.save(
            new_episode(
                "python", project="shop", card_id=n, run_id=n, role="builder", title=f"card {n}"
            )
        )
    AgentMemory(tmp_path, "critic").save(
        new_episode("critic", project="shop", card_id=1, run_id=1, role="critic", title="one")
    )
    answer = {"ideas": [{**GOOD, "kind": "skill", "project": None, "evidence": ["nope"]}]}
    runtime = FakeRuntime([{"op": "finish", "message": json.dumps(answer)}])
    report = asyncio.run(ideas.ideate(tmp_path, runtime, model=None))
    assert report.agents == {"python": 1}  # the critic has only one new episode
    idea = ideas.waiting(tmp_path)[0]
    assert (idea.source, idea.evidence) == ("dream", [])
    # Nothing new since: the next dream asks nobody.
    again = asyncio.run(ideas.ideate(tmp_path, runtime, model=None))
    assert again.agents == {}


def test_parse_ideas_tolerates_prose_and_caps_the_count() -> None:
    text = "Here you go:\n" + json.dumps({"ideas": [GOOD, GOOD, GOOD]})
    assert len(ideas.parse_ideas(text)) == ideas.MAX_PER_DREAM
    assert ideas.parse_ideas("nothing to propose") == []
