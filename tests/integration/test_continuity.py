"""Continuity: each agent writes its own story from its true record, every session opens with
it and the world the person wrote, aspirations are logged with dates, agents can ask for what
they need, and whether it helps is measured on a fixed half of the crew."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from prat import continuity, ideas, identity
from prat.memory import AgentMemory, Feeling, new_episode
from prat.runtime.fake import FakeRuntime


def introduce(home: Path, key: str = "python", name: str = "Marlow") -> None:
    identity.save(
        home, identity.Identity(key, name, "I build the APIs.", "calm", "eng", 1.0, "Do it well.")
    )


def test_the_world_is_the_persons_own_words_or_a_default(tmp_path: Path) -> None:
    assert "all you have to do is ask" in continuity.world(tmp_path)
    (tmp_path / "crew").mkdir()
    (tmp_path / "crew" / "world.md").write_text("I am Sam. We build tools for farmers.")
    assert continuity.world(tmp_path) == "I am Sam. We build tools for farmers."


def test_a_story_is_written_from_the_record_and_every_version_kept(tmp_path: Path) -> None:
    introduce(tmp_path)
    answer = {
        "story": "I chose the name Marlow. My first setup card failed four times before it "
        "landed, and it taught me to verify from a clean checkout.",
        "aspirations": ["land every setup first time"],
        "request": "I would like a way to see the production logs of the apps I set up.",
    }
    runtime = FakeRuntime([{"op": "finish", "message": json.dumps(answer)}])
    report = asyncio.run(continuity.write_stories(tmp_path, runtime, model=None))
    assert report.written == ["python"] and report.requests == 1
    story = continuity.story_of(tmp_path, "python")
    assert story.version == 1 and story.text.startswith("I chose the name Marlow")
    assert story.aspirations == ["land every setup first time"]
    request = ideas.waiting(tmp_path)[0]
    assert request.kind == "request" and "production logs" in request.proposal
    log = continuity.aspirations_log(tmp_path, "python")
    assert log[0]["items"] == ["land every setup first time"]
    # Not due again until a week has passed and there is new life to tell.
    assert not continuity.due(tmp_path, "python")
    later = time.time() + 8 * 86400
    assert not continuity.due(tmp_path, "python", now=later)
    AgentMemory(tmp_path, "python").save(
        new_episode("python", project="shop", card_id=1, run_id=1, role="builder", title="x")
    )
    assert continuity.due(tmp_path, "python", now=later)
    again = asyncio.run(continuity.write_stories(tmp_path, runtime, model=None, force=True))
    assert again.written == ["python"] and continuity.story_of(tmp_path, "python").version == 2
    lines = (tmp_path / "agents" / "python" / "story_log.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_an_agent_that_has_not_chosen_who_it_is_writes_no_story(tmp_path: Path) -> None:
    AgentMemory(tmp_path, "scout").save(
        new_episode("scout", project="shop", card_id=1, run_id=1, role="helper", title="x")
    )
    runtime = FakeRuntime([{"op": "finish", "message": '{"story": "x"}'}])
    assert asyncio.run(continuity.write_stories(tmp_path, runtime, model=None)).written == []


def test_the_opening_says_this_session_continues_the_agent(tmp_path: Path) -> None:
    introduce(tmp_path)
    continuity.save_story(tmp_path, "python", "I am Marlow and I care about clean checkouts.", [])
    text = continuity.opening(tmp_path, "python")
    assert "You chose that name" in text and "this one continues it" in text
    assert "I care about clean checkouts." in text and "all you have to do is ask" in text


def test_half_the_crew_gets_it_and_the_halves_are_compared(tmp_path: Path) -> None:
    keys = ["python", "laravel", "react", "node", "go", "php", "critic", "planner"]
    with_it = [k for k in keys if continuity.gets_it("half", k)]
    assert 0 < len(with_it) < len(keys)
    assert all(continuity.gets_it("all", k) for k in keys)
    assert not any(continuity.gets_it("off", k) for k in keys)
    since = continuity.started(tmp_path)
    assert continuity.started(tmp_path) == since  # fixed once
    lucky, other = with_it[0], next(k for k in keys if k not in with_it)
    for key, words in ((lucky, "proud"), (other, "satisfied")):
        AgentMemory(tmp_path, key).save(
            new_episode(
                key,
                project="shop",
                card_id=1,
                run_id=1,
                role="builder",
                title="x",
                outcome="landed",
                feeling=Feeling(words, 0.8, 0.6),
            )
        )
    _, (got, without) = continuity.compare(tmp_path, "half")
    assert (got.landed, got.first, got.first_rate) == (1, 1, 1.0)
    assert (without.landed, without.first, without.first_rate) == (1, 0, 0.0)


def test_the_ask_for_tool_files_a_request(tmp_path: Path) -> None:
    from prat.engine.agent_tools import ToolContext, build

    ctx = ToolContext(
        home=tmp_path,
        project="shop",
        card_id=4,
        run_id=2,
        agent="python",
        record=lambda *_: None,
        live=lambda *_: None,
    )
    tool = next(t for t in build(ctx) if t.name == "ask_for")
    answer = asyncio.run(
        tool.handler(
            {
                "what": "Read access to the staging database for this project",
                "why": "So I can check the migration against real row counts.",
            }
        )
    )
    assert "answers every one" in answer
    request = ideas.waiting(tmp_path)[0]
    assert (request.kind, request.card_id, request.project) == ("request", 4, "shop")


def test_the_half_can_be_chosen_to_split_the_busiest_agents() -> None:
    assert continuity.gets_it("half", "python", ["python", "critic"])
    assert not continuity.gets_it("half", "laravel", ["python", "critic"])
    assert continuity.gets_it("all", "laravel", ["python"])
