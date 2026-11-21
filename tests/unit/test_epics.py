"""Epic splits (prat.epics): the planner's prompt and what a part's card carries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from prat import epics


def epic(**values: Any) -> Any:
    base = {"id": 7, "title": "Storefront", "goal": "", "acceptance_criteria": ""}
    return cast(Any, SimpleNamespace(**{**base, **values}))


def test_the_split_prompt_asks_for_full_parts_or_for_references() -> None:
    full = epics.split_prompt(by_reference=False)
    brief = epics.split_prompt(by_reference=True)
    assert "testable statements, one per line" in full and "never copy it" not in full
    assert "never copy it" in brief
    for prompt in (full, brief):
        assert '{"one_card": true}' in prompt and "{brief}" not in prompt
        assert f"Between {epics.MIN_PARTS} and {epics.MAX_PARTS} parts" in prompt


def test_a_part_written_in_full_carries_only_its_own_goal() -> None:
    goal = epics.part_goal("Build wishlists.", epic(goal="Five additions."), by_reference=False)
    assert goal == "Build wishlists.\n\nPart of epic #7: Storefront."


def test_a_part_by_reference_carries_the_whole_epic_marked_as_reference() -> None:
    whole = epic(goal="1. Wishlists: add(customer, sku).\n2. Reviews.", acceptance_criteria="- a")
    goal = epics.part_goal("Section 1, tinyshop/wishlist.py.", whole, by_reference=True)
    own, _, reference = goal.partition("## The whole epic, for reference")
    assert own.startswith("Section 1, tinyshop/wishlist.py.\n\nPart of epic #7: Storefront.")
    assert "build and test only the piece described above" in own
    assert "1. Wishlists: add(customer, sku)." in reference and "2. Reviews." in reference
    assert reference.rstrip().endswith("The epic's acceptance criteria:\n- a")


def test_an_epic_with_no_text_adds_no_empty_criteria() -> None:
    goal = epics.part_goal("Part one.", epic(), by_reference=True)
    assert "acceptance criteria:" not in goal.partition("## The whole epic")[2]
