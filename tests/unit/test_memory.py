"""Agent memory: working notes, episodes, recall weighted like a person's, outcomes that change
how a memory feels, beliefs from consolidation."""

from __future__ import annotations

import time
from pathlib import Path

from prat.consolidate import fold_working, parse_beliefs
from prat.memory import AgentMemory, Belief, Feeling, briefing, new_episode, settle


def episode(memory: AgentMemory, title: str, **values: object) -> str:
    item = new_episode(
        memory.agent,
        project="shop",
        card_id=values.pop("card_id", 1),
        run_id=1,
        role="builder",
        title=title,
        **values,  # type: ignore[arg-type]
    )
    memory.save(item)
    return item.id


def test_working_memory_keeps_notes_per_card(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path, "Pip")
    memory.note("shop", 7, "thought", "the cart total ignores tax")
    memory.note("shop", 7, "feeling", "uneasy about the rounding", Feeling("uneasy", -0.3, 0.5))
    notes = memory.working("shop", 7)
    assert [n.kind for n in notes] == ["thought", "feeling"]
    assert notes[1].feeling is not None and notes[1].feeling.words == "uneasy"
    assert memory.working("shop", 8) == []


def test_recall_finds_relevant_work_and_strong_feelings_weigh_more(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path, "pip")
    episode(memory, "Add tax to cart totals", what="cart totals now include tax", card_id=1)
    calm = episode(memory, "Rename cart helper", what="cart helper renamed", card_id=2)
    hurt = episode(
        memory,
        "Cart rounding fix",
        what="cart rounding changed",
        feeling=Feeling("ashamed", -0.9, 0.9),
        card_id=3,
    )
    episode(memory, "Logo update", what="new logo", card_id=4)
    found = memory.recall("cart rounding")
    assert found[0].id == hurt
    assert all(e.title != "Logo update" for e in found)
    ids = [e.id for e in memory.recall("cart helper")]
    assert ids[0] == calm


def test_an_update_to_an_episode_replaces_it(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path, "pip")
    episode(memory, "Add tax", card_id=5)
    settled = settle(
        tmp_path, project="shop", card_id=5, outcome="reworked", feedback="tax on shipping too"
    )
    assert settled == 1
    (only,) = memory.episodes()
    assert only.outcome == "reworked" and "shipping" in only.feedback
    assert only.feeling.valence < 0 and only.importance > 0.5
    memory.compact()
    assert len(memory.episodes_path.read_text(encoding="utf-8").splitlines()) == 1


def test_landing_on_the_first_review_is_remembered_proudly(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path, "pip")
    episode(memory, "Add tax", card_id=6)
    settle(tmp_path, project="shop", card_id=6, outcome="landed", first_review=True)
    (only,) = memory.episodes()
    assert only.outcome == "landed" and "proud" in only.feeling.words


def test_the_briefing_holds_beliefs_past_work_and_this_cards_notes(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path, "pip")
    memory.save_beliefs(
        [Belief("b1", "In shop, money is stored in cents.", project="shop", strength=0.9)]
    )
    episode(
        memory,
        "Cart totals in cents",
        what="switched totals to integer cents",
        lesson="never use floats for money",
        card_id=9,
    )
    memory.note("shop", 10, "plan", "check the invoice export too")
    text = briefing(memory, project="shop", card_id=10, query="invoice totals cents")
    assert "money is stored in cents" in text
    assert "never use floats for money" in text
    assert "check the invoice export too" in text


def test_finished_cards_fold_their_notes_into_the_episode(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path, "pip")
    episode(memory, "Add tax", card_id=11)
    memory.note("shop", 11, "note", "tax table lives in config/tax.php")
    assert fold_working(tmp_path, "pip") == 0  # not finished yet
    settle(tmp_path, project="shop", card_id=11, outcome="landed")
    assert fold_working(tmp_path, "pip") == 1
    assert "config/tax.php" in memory.episodes()[0].reasoning
    assert memory.working("shop", 11) == []


def test_consolidated_beliefs_cite_only_real_episodes() -> None:
    text = (
        'Sure. {"beliefs": [{"text": "Run migrations on the branch", "project": null, '
        '"strength": 2, "evidence": ["a", "zz"]}]}'
    )
    (belief,) = parse_beliefs(text, {"a"})
    assert belief.strength == 1.0 and belief.evidence == ["a"] and belief.project is None


def test_recency_fades_old_memories(tmp_path: Path) -> None:
    memory = AgentMemory(tmp_path, "pip")
    old = new_episode("pip", project="shop", card_id=1, run_id=1, role="builder", title="cart work")
    old.at = time.time() - 400 * 86400
    memory.save(old)
    episode(memory, "cart work", card_id=2)
    assert memory.recall("cart work")[0].card_id == 2
