"""Design standards: what a screen is held to, with where each rule comes from.

PRAT does not invent a taste. ``resources/standards/design.toml`` holds published, generally
accepted rules for interfaces, each with its source, the number to check where there is one,
the same rule as a plain "do not", and real pages that show it done well. A builder of an
interface card is given the rules with their examples before it writes anything; the rules a
machine measures are measured from the page (prat.screens, prat.pagecheck); the rest are the
questions a reviewer answers about the pictures of the page, one by one, instead of giving an
opinion.

The design set is one of several (prat.standards): accessibility, performance, search and AI
assistants are kept the same way. A project adds only what is its own: the values its screens
are made of (its ``brand.toml``) and, in its notes, anything a person there prefers. Those are
preferences, not standards, and are never mixed into these files.
"""

from __future__ import annotations

import re
from typing import Any

from prat import standards as _standards
from prat.standards import Example, Rule

__all__ = ["Example", "Rule"]

HEADING = "## Design standards for this card's screens"
# Words in a card that make it interface work before a line of it is written.
INTERFACE = re.compile(
    r"\b(ui|ux|screen|page|layout|button|form|modal|dialog|menu|navigation|navbar|sidebar|"
    r"header|footer|css|style|styling|theme|responsive|mobile|component|view|icon|tooltip|"
    r"dropdown|tab|tabs|card|banner|toast|design|front[- ]?end|interface|panel|popup|"
    r"popover|drawer|accordion|carousel|checkbox|toggle|slider|input|field|label|font|"
    r"colou?r|spacing|align|alignment|breakpoint|viewport|seo|sitemap|landing|homepage|"
    r"article|blog|accessibility|a11y|lighthouse|web vitals)\b",
    re.I,
)


def parse(data: dict[str, Any]) -> list[Rule]:
    return _standards.parse(data, "design")


def standards() -> tuple[Rule, ...]:
    """The design rules PRAT ships with; none when the file is missing or does not parse."""
    return _standards.load("design")


def is_interface(text: str) -> bool:
    """Whether a card's own words say it is interface work."""
    return len(set(m.group(0).lower() for m in INTERFACE.finditer(text))) >= 2


def for_builder(rules: tuple[Rule, ...] | None = None, *, examples: int = 2) -> str:
    """The design rules as a builder is given them."""
    return _standards.for_builder(
        standards() if rules is None else rules, examples=examples, title=HEADING
    )


def example_for(rule_id: str, rules: tuple[Rule, ...] | None = None) -> str:
    """One rule in full, with what each of its example pages shows."""
    rules = standards() if rules is None else rules
    return _standards.example_for(next((r for r in rules if r.id == rule_id), None))


def checklist(rules: tuple[Rule, ...] | None = None) -> str:
    """The design rules nothing measures, as questions for a reviewer."""
    return _standards.checklist(standards() if rules is None else rules, title="Design")
