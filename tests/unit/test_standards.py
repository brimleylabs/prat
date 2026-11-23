"""The standard sets PRAT ships and how they are given out (prat.standards)."""

from __future__ import annotations

import pytest

from prat import pagecheck, standards


@pytest.mark.parametrize("name", standards.SETS)
def test_every_rule_of_every_set_names_its_source_and_real_examples(name: str) -> None:
    rules = standards.load(name)
    assert len(rules) >= 20, name
    assert len({rule.id for rule in rules}) == len(rules)
    for rule in rules:
        assert rule.set == name
        assert rule.rule and rule.source and rule.source_url.startswith("https://"), rule.id
        assert rule.check in ("mechanical", "judgment"), rule.id
        assert rule.basis in standards.BASIS, rule.id
        assert 2 <= len(rule.examples) <= 3, rule.id
        for example in rule.examples:
            assert example.name and example.url.startswith("https://") and example.shows, rule.id
    told = standards.for_builder(rules) + standards.for_builder(rules, brief=True)
    # No dashes that read as machine-written, anywhere in what builders and reviewers are given.
    assert "—" not in told + standards.checklist(rules)


def test_no_rule_is_in_two_sets_under_one_name() -> None:
    seen: dict[str, str] = {}
    for name in ("design", "accessibility"):
        for rule in standards.load(name):
            assert rule.id not in seen, f"{rule.id} is in {seen.get(rule.id)} and {name}"
            seen[rule.id] = name


def test_only_what_is_measured_is_called_measured() -> None:
    rules = standards.parse(
        {
            "rule": [
                {
                    "id": "image-alt-present",
                    "area": "perceivable",
                    "rule": "Every image has a text alternative.",
                    "measure": "An alt attribute on every img",
                    "check": "mechanical",
                    "source": "WCAG 2.2, 1.1.1",
                    "source_url": "https://www.w3.org/TR/WCAG22/#non-text-content",
                    "do_not": "Do not leave alt off.",
                    "example": [
                        {"name": "A", "url": "https://a.example/x", "shows": "Alt text."},
                        {"name": "B", "url": "https://b.example/y", "shows": "Also."},
                    ],
                },
                {
                    "id": "skip-link",
                    "area": "operable",
                    "rule": "Pages start with a skip link.",
                    "measure": "x" * 200,
                    "check": "mechanical",
                    "basis": "vendor",
                    "source": "GOV.UK",
                    "source_url": "https://g.example/skip",
                },
            ]
        },
        "accessibility",
    )
    full = standards.for_builder(rules)
    assert full.startswith("## Accessibility standards for this card's pages")
    assert "Every image has a text alternative. Number: An alt attribute on every img." in full
    assert "[WCAG 2.2, 1.1.1] (measured)" in full and "Do not leave alt off." in full
    # A machine could decide the skip link, but nothing here does: it is not called measured.
    assert "[GOV.UK; vendor guidance]\n" in full + "\n" and "skip link. Number: xxx" in full
    brief = standards.for_builder(rules, brief=True)
    assert "Do not leave alt off." not in brief and "b.example" not in brief
    assert "Done well: A <https://a.example/x>" in brief
    assert "xxx" not in brief  # a number that is a paragraph is left for when it is needed
    asked = standards.checklist(rules)
    assert asked.startswith("Accessibility checklist.")
    assert "[skip-link] Pages start with a skip link. [GOV.UK; vendor guidance]" in asked
    assert "image-alt-present" not in asked
    assert standards.example_for(rules[0]).count("https://") == 3
    assert standards.example_for(None) == "" and standards.for_builder(()) == ""


def test_a_builder_reads_the_sets_that_apply_and_no_more() -> None:
    app = standards.builder_text(pagecheck.sets_for(website=False))
    site = standards.builder_text(pagecheck.sets_for(website=True))
    assert "## Search standards" not in app and "## AI search standards" not in app
    for name in ("Design", "Accessibility", "Performance", "Search", "AI search"):
        assert f"## {name} standards for this card's pages" in site
    assert len(app) < 50_000 and len(site) < 80_000  # what a builder reads has a ceiling
    assert standards.builder_text(()) == "" and standards.reviewer_text(()) == ""
    assert standards.find("lcp", "performance") is not None and standards.find("nope") is None
