"""The social hour: colleagues talk before work, turns follow who was addressed, each grows an
inner life from it, every conversation is kept, and the person can say good morning."""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any

from prat import identity, social
from prat.memory import AgentMemory
from prat.runtime.base import AgentTask
from prat.runtime.fake import FakeRuntime, FakeScript

CREW = {"python": "Marlow", "laravel": "Odile", "critic": "Rowan"}


def seeded(seed: int) -> random.Random:
    return random.Random(seed)  # noqa: S311 (a repeatable test, not security)


def introduce_crew(home: Path, keys: dict[str, str] = CREW) -> None:
    for key, name in keys.items():
        identity.save(
            home,
            identity.Identity(
                key, name, f"I am {name}.", "warm, curious", "eng", 1.0, "Do it well."
            ),
        )


def scripted(lines: dict[str, list[str]], reflection: dict[str, Any]) -> FakeRuntime:
    """Each agent says its lines in turn; any reflection prompt gets ``reflection``."""
    said: dict[str, int] = {}

    def script(task: AgentTask) -> FakeScript:
        key = str(task.metadata.get("agent"))
        if "social hour is over" in task.prompt:
            text = json.dumps(reflection)
        else:
            n = said.get(key, 0)
            said[key] = n + 1
            options = lines.get(key) or ["Morning."]
            text = options[min(n, len(options) - 1)]
        return FakeScript.parse([{"op": "finish", "message": text}])

    return FakeRuntime(script)


REFLECTION = {
    "memory": "It was good to hear how Odile thinks about databases.",
    "feeling": "warm",
    "valence": 0.6,
    "intensity": 0.5,
    "purpose": "I want the people who use what we build to trust it.",
    "hopes": ["to land every setup card first time"],
    "wishes": ["more time to read the old code"],
    "interests": ["how databases recover"],
    "colleagues": {"Odile": "Careful and funny.", "Nobody": "ignored"},
}


def test_a_social_hour_is_held_logged_and_grows_each_inner_life(tmp_path: Path) -> None:
    introduce_crew(tmp_path)
    runtime = scripted(
        {
            "python": ["Morning! Odile, how did the Dolt move go?", "Ha, fair enough."],
            "laravel": ["Better than I feared, Marlow. Rowan, you were quiet yesterday."],
            "critic": ["Just thinking about review checklists, Odile."],
        },
        REFLECTION,
    )
    met = asyncio.run(social.gather(tmp_path, runtime, model=None, size=3, rounds=2, rng=seeded(1)))
    assert met is not None and len(met.turns) == 6
    # Nobody speaks twice in a row, and the colleague addressed by name answers next.
    for before, after in zip(met.turns, met.turns[1:], strict=False):
        assert before.agent != after.agent
        addressed = [k for k, n in CREW.items() if n in before.text and k != before.agent]
        if addressed:
            assert after.agent in addressed
    # Kept in full, and readable back.
    kept = social.gatherings(tmp_path)
    assert kept[0].id == met.id and [t.text for t in kept[0].turns] == [t.text for t in met.turns]
    inner = social.inner_of(tmp_path, "python")
    assert inner.purpose.startswith("I want the people")
    assert inner.hopes == ["to land every setup card first time"]
    assert inner.colleagues == {"laravel": "Careful and funny."}
    assert inner.socials == 1 and inner.last_social > 0
    memory = [e for e in AgentMemory(tmp_path, "python").episodes() if e.role == "social"]
    assert memory and memory[0].feeling.words == "warm"
    text = social.inner_text(tmp_path, "python")
    assert "Your purpose" in text and "Odile: Careful and funny." in text


def test_everyone_meets_over_time(tmp_path: Path) -> None:
    introduce_crew(tmp_path, {**CREW, "react": "Tobin", "go": "Calder"})
    first = set(social.invite(tmp_path, 3, rng=seeded(2)))
    for key in first:
        inner = social.inner_of(tmp_path, key)
        inner.last_social = 1.0
        social.save_inner(tmp_path, key, inner)
    second = set(social.invite(tmp_path, 3, rng=seeded(2)))
    assert {"python", "laravel", "critic", "react", "go"} - first <= second


def test_fewer_than_two_agents_do_not_meet(tmp_path: Path) -> None:
    introduce_crew(tmp_path, {"python": "Marlow"})
    runtime = scripted({}, REFLECTION)
    assert asyncio.run(social.gather(tmp_path, runtime, model=None)) is None


def test_the_person_says_good_morning_and_those_named_answer(tmp_path: Path) -> None:
    introduce_crew(tmp_path)
    runtime = scripted(
        {
            "laravel": ["Good morning! It went well, thank you."],
            "python": ["Morning!"],
            "critic": ["Morning."],
        },
        REFLECTION,
    )
    met = asyncio.run(
        social.say_hello(
            tmp_path, runtime, "Good morning, Odile. How did yesterday go?", model=None
        )
    )
    assert met.turns[0].agent == social.PERSON
    assert met.turns[1].agent == "laravel"
    assert len([t for t in met.turns if t.agent != social.PERSON]) == social.GREETED
    remembered = [e for e in AgentMemory(tmp_path, "laravel").episodes() if e.role == "social"]
    assert remembered and "Good morning, Odile" in remembered[0].what
    # Saying hello again joins the same morning's conversation.
    again = asyncio.run(social.say_hello(tmp_path, runtime, "Have a good day, all.", model=None))
    assert again.id == met.id and len(social.gatherings(tmp_path)) == 1


def test_turns_lose_a_name_prefix_and_stay_short() -> None:
    assert social._clean_turn("**Marlow:** Morning all", "Marlow") == "Morning all"
    assert len(social._clean_turn("word " * 400, "Marlow")) <= social.TURN_CHARS


def test_someone_else_saying_hello_is_remembered_under_their_own_name(tmp_path: Path) -> None:
    introduce_crew(tmp_path)
    runtime = scripted({"laravel": ["Nice to meet you."]}, REFLECTION)
    met = asyncio.run(
        social.say_hello(
            tmp_path, runtime, "Hello Odile, I'm Matt's assistant.", model=None, speaker="Claude"
        )
    )
    assert met.turns[0].name == "Claude"
    remembered = [e for e in AgentMemory(tmp_path, "laravel").episodes() if e.role == "social"]
    assert remembered[0].title.startswith("Claude said") and "Matt said" not in remembered[0].what


def test_an_agent_knows_what_its_night_was(tmp_path: Path) -> None:
    import time as clock

    from prat import ideas
    from prat.memory import Belief

    introduce_crew(tmp_path)
    assert "nothing new to look back on" in social.last_night(tmp_path, "python")
    memory = AgentMemory(tmp_path, "python")
    memory.save_beliefs([Belief("b1", "Run the gates from a clean checkout.")])
    (memory.root / "consolidated.json").write_text(json.dumps({"at": clock.time()}))
    ideas.propose(
        tmp_path,
        "python",
        title="Quote titles with colons",
        problem="Seven notes had invalid YAML titles and needed a fix round.",
        proposal="Quote any title containing a colon, and parse frontmatter before review.",
        source="dream",
    )
    night = social.last_night(tmp_path, "python")
    assert "1 belief you now hold" in night and "clean checkout" in night
    assert '"Quote titles with colons"' in night


def test_of_several_colleagues_addressed_the_quietest_answers() -> None:
    names = {"python": "Marlow", "laravel": "Odile", "react": "Tobin"}
    gathering = social.Gathering("g", 0.0, list(names))
    gathering.turns = [
        social.SocialTurn("laravel", "Odile", "Hello all.", 1.0),
        social.SocialTurn("python", "Marlow", "Odile, Tobin: what do you find beautiful?", 2.0),
    ]
    for seed in range(10):
        assert social.next_speaker(gathering, names, seeded(seed)) == "react"
