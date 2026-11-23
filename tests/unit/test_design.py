"""The design standards PRAT ships and how they are given out (prat.design)."""

from __future__ import annotations

from prat import design


def test_every_rule_names_its_source_and_real_examples() -> None:
    rules = design.standards()
    assert len(rules) >= 25
    assert len({rule.id for rule in rules}) == len(rules)
    for rule in rules:
        assert rule.rule and rule.source and rule.source_url.startswith("https://"), rule.id
        assert rule.check in ("mechanical", "judgment"), rule.id
        assert 2 <= len(rule.examples) <= 3, rule.id
        for example in rule.examples:
            assert example.name and example.url.startswith("https://") and example.shows, rule.id
    # No dashes that read as machine-written, anywhere in what builders are given.
    assert "—" not in design.for_builder() + design.checklist()


def test_builders_get_rules_with_examples_and_reviewers_get_questions() -> None:
    rules = design.parse(
        {
            "rule": [
                {
                    "id": "contrast-text",
                    "area": "legibility",
                    "rule": "Text contrast is at least 4.5 to 1.",
                    "measure": "4.5:1",
                    "check": "mechanical",
                    "source": "WCAG 2.2, 1.4.3",
                    "source_url": "https://www.w3.org/TR/WCAG22/#contrast-minimum",
                    "do_not": "Do not put light grey text on white.",
                    "example": [
                        {"name": "A", "url": "https://a.example/x", "shows": "High contrast."},
                        {"name": "B", "url": "https://b.example/y", "shows": "Also."},
                        {"name": "C", "url": "https://c.example/z", "shows": "Third."},
                    ],
                },
                {
                    "id": "one-primary-action",
                    "area": "hierarchy",
                    "rule": "A view has one primary action.",
                    "source": "A design system",
                    "source_url": "https://d.example/buttons",
                },
            ]
        }
    )
    told = design.for_builder(tuple(rules))
    assert told.startswith(design.HEADING)
    assert "Text contrast is at least 4.5 to 1. Number: 4.5:1. [WCAG 2.2, 1.4.3] (measured)" in told
    assert "Do not put light grey text on white." in told
    assert "Done well: A <https://a.example/x>; B <https://b.example/y>" in told
    assert "c.example" not in told  # two examples each is enough to carry
    assert "### Hierarchy" in told
    asked = design.checklist(tuple(rules))
    assert "[one-primary-action] A view has one primary action." in asked
    assert "contrast-text" not in asked  # a machine measures that one
    full = design.example_for("contrast-text", tuple(rules))
    assert "https://c.example/z: Third." in full and design.example_for("nope", tuple(rules)) == ""
    assert design.for_builder(()) == "" and design.checklist(()) == ""


def test_a_card_is_interface_work_by_its_own_words() -> None:
    assert design.is_interface("Add a filter button to the planner page sidebar")
    assert design.is_interface("Make the booking form responsive on mobile")
    assert not design.is_interface("Fix the fingerprint remap in the collector")
    assert not design.is_interface("Speed up the nightly export job")
