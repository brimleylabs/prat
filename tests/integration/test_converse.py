"""Agents choose who they are, talk with their person, give stand-ups and want to succeed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prat import converse, identity
from prat.config import Settings
from prat.db import Database
from prat.memory import AgentMemory, Feeling, new_episode, track_record
from prat.runtime import FakeRuntime

pytestmark = pytest.mark.integration


def says(text: str) -> FakeRuntime:
    """A runtime whose every session answers ``text``."""
    return FakeRuntime([{"op": "finish", "message": text}])


def test_an_agent_chooses_a_proper_name() -> None:
    good = json.dumps(
        {
            "name": "Mara",
            "bio": "I build the APIs.",
            "personality": "calm",
            "drive": "I want it to land.",
        }
    )
    found = identity.parse(good, "python", taken=set())
    assert found is not None and found.name == "Mara" and found.drive == "I want it to land."
    assert identity.parse(good, "python", taken={"mara"}) is None  # a crewmate has it
    assert identity.parse(good.replace("Mara", "Claude"), "python", set()) is None
    assert identity.parse(good.replace("Mara", "two words"), "python", set()) is None


def test_the_persona_carries_the_voice_and_the_drive(tmp_path: Path) -> None:
    who = identity.Identity(
        "python",
        "Mara",
        "I build APIs.",
        "calm",
        "the Python engineer",
        1.0,
        "Shipping right matters to me.",
    )
    text = who.persona()
    assert "You are Mara" in text and "English" in text and "courteous" in text
    assert "want this work to succeed" in text and "weakening a test" in text
    assert "Shipping right matters to me." in text
    identity.save(tmp_path, who)
    assert identity.of(tmp_path, "python").name == "Mara"
    assert identity.of(tmp_path, "scout").name == "Scout"  # not introduced yet


async def test_an_introduction_is_kept(settings: Settings) -> None:
    runtime = says(json.dumps({"name": "Iris", "bio": "I find things.", "personality": "curious"}))
    who = await identity.introduce(settings.home, runtime, "scout", model=None)
    assert who.name == "Iris" and who.chosen
    assert identity.load(settings.home, "scout") is not None


async def test_talking_is_remembered_and_thanks_feel_good(settings: Settings, db: Database) -> None:
    identity.save(
        settings.home, identity.Identity("python", "Mara", "I build APIs.", "calm", "eng", 1.0)
    )
    runtime = says("You're welcome. Today I finished the cart totals and I'm pleased with it.")
    reply = await converse.talk(
        settings.home, db, runtime, "python", "Thank you for the cart work!", model=None
    )
    assert "cart totals" in reply
    turns = converse.history(settings.home, "python")
    assert [t.who for t in turns] == ["person", "python"]
    (episode,) = AgentMemory(settings.home, "python").episodes()
    assert episode.feeling.words == "appreciated" and episode.feeling.valence > 0


async def test_a_standup_hears_from_everyone_who_worked(settings: Settings, db: Database) -> None:
    identity.save(
        settings.home, identity.Identity("python", "Mara", "I build APIs.", "calm", "eng", 1.0)
    )
    AgentMemory(settings.home, "python").save(
        new_episode(
            "python", project="shop", card_id=3, run_id=1, role="builder", title="Cart totals"
        )
    )
    runtime = says("Done: cart totals. Doing: nothing. Blockers: none. Feeling: good.")
    (report,) = await converse.standup(settings.home, db, runtime, model=None)
    assert report.name == "Mara" and "cart totals" in report.text


def test_the_track_record_counts_landings_streaks_and_resolutions(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path, "python")
    for card, outcome in ((1, "reworked"), (2, "landed"), (3, "landed")):
        episode = new_episode(
            "python", project="shop", card_id=card, run_id=1, role="builder", title=f"c{card}"
        )
        episode.outcome = outcome
        episode.feeling = Feeling("proud" if card == 3 else "ok", 0.5, 0.5)
        episode.intention = "write the failing test first" if card == 1 else ""
        memory.save(episode)
    text = track_record(memory)
    assert "2 cards landed (1 on the first review), 1 sent back" in text
    assert "2 in a row" in text and "write the failing test first" in text
