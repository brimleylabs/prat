"""Design standards: what a screen is held to, with where each rule comes from.

PRAT does not invent a taste. ``resources/design/standards.toml`` holds published, generally
accepted rules for interfaces, each with its source, the number to check where there is one,
the same rule as a plain "do not", and real pages that show it done well. A builder of an
interface card is given the rules with their examples before it writes anything; the rules a
machine can measure are measured from the page (prat.screens); the rest are the questions a
reviewer answers about the pictures of the page, one by one, instead of giving an opinion.

A project adds only what is its own: the values its screens are made of (its ``brand.toml``)
and, in its notes, anything a person there prefers. Those are preferences, not standards, and
are never mixed into this file.
"""

from __future__ import annotations

import functools
import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from typing import Any

HEADING = "## Design standards for this card's screens"
# Words in a card that make it interface work before a line of it is written.
INTERFACE = re.compile(
    r"\b(ui|ux|screen|page|layout|button|form|modal|dialog|menu|navigation|navbar|sidebar|"
    r"header|footer|css|style|styling|theme|responsive|mobile|component|view|icon|tooltip|"
    r"dropdown|tab|tabs|card|banner|toast|design|front[- ]?end|interface|panel|popup|"
    r"popover|drawer|accordion|carousel|checkbox|toggle|slider|input|field|label|font|"
    r"colou?r|spacing|align|alignment|breakpoint|viewport)\b",
    re.I,
)


@dataclass(frozen=True)
class Example:
    name: str
    url: str
    shows: str


@dataclass(frozen=True)
class Rule:
    id: str
    area: str
    rule: str
    measure: str
    check: str  # "mechanical" or "judgment"
    source: str
    source_url: str
    do_not: str
    examples: tuple[Example, ...] = ()


def parse(data: dict[str, Any]) -> list[Rule]:
    rules = []
    for raw in data.get("rule") or []:
        rules.append(
            Rule(
                id=str(raw["id"]),
                area=str(raw.get("area") or ""),
                rule=str(raw["rule"]),
                measure=str(raw.get("measure") or ""),
                check=str(raw.get("check") or "judgment"),
                source=str(raw.get("source") or ""),
                source_url=str(raw.get("source_url") or ""),
                do_not=str(raw.get("do_not") or ""),
                examples=tuple(
                    Example(str(e["name"]), str(e["url"]), str(e.get("shows") or ""))
                    for e in raw.get("example") or []
                ),
            )
        )
    return rules


@functools.cache
def standards() -> tuple[Rule, ...]:
    """The rules PRAT ships with; none when the file is missing or does not parse."""
    try:
        text = (resources.files("prat") / "resources" / "design" / "standards.toml").read_text(
            "utf-8"
        )
        return tuple(parse(tomllib.loads(text)))
    except (OSError, ValueError, KeyError, ModuleNotFoundError):
        return ()


def is_interface(text: str) -> bool:
    """Whether a card's own words say it is interface work."""
    return len(set(m.group(0).lower() for m in INTERFACE.finditer(text))) >= 2


def for_builder(rules: tuple[Rule, ...] | None = None, *, examples: int = 2) -> str:
    """The rules as a builder is given them: each with its source, its number, its "do not",
    and pages that show it done well."""
    rules = standards() if rules is None else rules
    if not rules:
        return ""
    lines = [
        HEADING,
        "",
        "These are published standards, not preferences. Follow them unless the card or the "
        "project's own brand values say otherwise. PRAT measures the ones marked (measured) "
        "on the page when you finish and hands back what fails. Before you build a control or "
        "a layout, use the component and the pattern this project already has for that job; "
        "where it has none, the pages named under a rule show it done well.",
    ]
    area = ""
    for rule in rules:
        if rule.area != area:
            area = rule.area
            lines += ["", f"### {area.capitalize()}"]
        measured = " (measured)" if rule.check == "mechanical" else ""
        number = f" Number: {rule.measure}." if rule.measure else ""
        lines.append(f"- {rule.rule}{number} [{rule.source}]{measured}")
        if rule.do_not:
            lines.append(f"  {rule.do_not}")
        shown = "; ".join(f"{e.name} <{e.url}>" for e in rule.examples[:examples])
        if shown:
            lines.append(f"  Done well: {shown}")
    return "\n".join(lines)


def example_for(rule_id: str, rules: tuple[Rule, ...] | None = None) -> str:
    """One rule in full, with what each of its example pages shows: what a builder is pointed
    to when a measured fault comes back."""
    rules = standards() if rules is None else rules
    rule = next((r for r in rules if r.id == rule_id), None)
    if rule is None:
        return ""
    lines = [f"{rule.rule} [{rule.source}, {rule.source_url}]"]
    lines += [f"- {e.name}, {e.url}: {e.shows}" for e in rule.examples]
    return "\n".join(lines)


def checklist(rules: tuple[Rule, ...] | None = None) -> str:
    """The rules a machine cannot measure, as questions a reviewer answers about the pictures
    of a page."""
    rules = standards() if rules is None else rules
    asked = [rule for rule in rules if rule.check != "mechanical"]
    if not asked:
        return ""
    lines = [
        "Design checklist. Look at the pictures and answer each with yes, no or cannot tell, "
        "naming the element for every no. A no is a finding only when you can point at it in "
        "a picture or in the diff; do not add opinions that are not on this list.",
    ]
    lines += [f"- [{rule.id}] {rule.rule} [{rule.source}]" for rule in asked]
    return "\n".join(lines)
