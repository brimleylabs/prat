"""The published standards PRAT holds a project's pages to, one file per subject.

``resources/standards/<set>.toml`` holds generally accepted rules, each with where it comes
from, how firm that source is (``basis``), the number to check where there is one, the same
rule as a plain "do not", and real pages that show it done well:

``design``         layout, legibility, forms, feedback, navigation
``accessibility``  WCAG 2.2 A and AA beyond what ``design`` already covers
``performance``    the Core Web Vitals and the practices that move them
``seo``            what search engines document about crawling, indexing and page metadata
``llm``            what AI search products are documented to crawl, read and cite

A builder is given the sets that apply to its card before it writes anything. The rules PRAT
measures on the page (prat.screens, prat.pagecheck) are marked so; every other rule becomes a
question a reviewer answers one by one. PRAT never says a rule is measured when nothing
measures it.
"""

from __future__ import annotations

import functools
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any

from prat import pagecheck

SETS = pagecheck.SETS
TITLES = {
    "design": "Design",
    "accessibility": "Accessibility",
    "performance": "Performance",
    "seo": "Search",
    "llm": "AI search",
}
# Design rules prat.screens measures from where a page's elements are.
LAYOUT_MEASURED = frozenset(
    {"contrast-text", "target-size-min", "reflow-and-zoom", "no-truncation"}
)
# How firm a rule's source is, as a builder and a reviewer are told.
BASIS = {
    "standard": "",
    "vendor": "vendor guidance",
    "research": "research finding",
    "proposal": "proposal, not adopted",
}


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
    check: str  # "mechanical" or "judgment": whether a machine could decide it
    source: str
    source_url: str
    do_not: str
    examples: tuple[Example, ...] = ()
    basis: str = "standard"
    probe: str = ""
    set: str = "design"


def parse(data: dict[str, Any], set_name: str = "design") -> list[Rule]:
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
                basis=str(raw.get("basis") or "standard"),
                probe=str(raw.get("probe") or ""),
                set=set_name,
            )
        )
    return rules


@functools.cache
def load(set_name: str) -> tuple[Rule, ...]:
    """The rules of one set as PRAT ships them; none when the file is missing or does not
    parse."""
    try:
        text = (resources.files("prat") / "resources" / "standards" / f"{set_name}.toml").read_text(
            "utf-8"
        )
        return tuple(parse(tomllib.loads(text), set_name))
    except (OSError, ValueError, KeyError, ModuleNotFoundError):
        return ()


def measured(rule: Rule) -> bool:
    """Whether PRAT itself measures this rule on a page."""
    if rule.set == "design" and rule.id in LAYOUT_MEASURED:
        return True
    return rule.id in pagecheck.MEASURED.get(rule.set, frozenset())


def heading(set_name: str) -> str:
    return f"## {TITLES.get(set_name, set_name.capitalize())} standards for this card's pages"


INTRO = (
    "These are published standards, not preferences. Follow them unless the card or the "
    "project's own brand values say otherwise. PRAT measures the ones marked (measured) "
    "on the page when you finish and hands back what fails. Before you build a control or "
    "a layout, use the component and the pattern this project already has for that job; "
    "where it has none, the pages named under a rule show it done well."
)
BRIEF_INTRO = (
    "Published standards, each with a page that shows it done well. PRAT measures the ones "
    "marked (measured) when you finish; a rule you break comes back with its number and its "
    "examples in full."
)
# In the brief form a rule's number is given only when it is short enough to be one.
BRIEF_MEASURE = 120


def for_builder(
    rules: Sequence[Rule], *, examples: int = 2, title: str | None = None, brief: bool = False
) -> str:
    """Rules as a builder is given them: each with its source, its number, its "do not", and
    pages that show it done well. ``brief`` is the rule, its source and one page, for the sets
    a builder is given beside the design set: what it reads has to stay in proportion."""
    if not rules:
        return ""
    lines = [title or heading(rules[0].set), "", BRIEF_INTRO if brief else INTRO]
    area = ""
    for rule in rules:
        if rule.area != area:
            area = rule.area
            lines += ["", f"### {area.capitalize()}"]
        mark = " (measured)" if measured(rule) else ""
        short = not brief or len(rule.measure) <= BRIEF_MEASURE
        number = f" Number: {rule.measure.rstrip('.')}." if rule.measure and short else ""
        firm = BASIS.get(rule.basis, rule.basis)
        source = f"{rule.source}; {firm}" if firm else rule.source
        lines.append(f"- {rule.rule}{number} [{source}]{mark}")
        if rule.do_not and not brief:
            lines.append(f"  {rule.do_not}")
        shown = "; ".join(f"{e.name} <{e.url}>" for e in rule.examples[: 1 if brief else examples])
        if shown:
            lines.append(f"  Done well: {shown}")
    return "\n".join(lines)


def builder_text(sets: Sequence[str]) -> str:
    """Every rule of the sets asked for, as one section each: the design set in full, the
    others in brief."""
    parts = [for_builder(load(name), brief=name != "design") for name in SETS if name in sets]
    return "\n\n".join(part for part in parts if part)


def find(rule_id: str, set_name: str | None = None) -> Rule | None:
    names = (set_name,) if set_name else SETS
    return next((r for name in names for r in load(name) if r.id == rule_id), None)


def example_for(rule: Rule | None) -> str:
    """One rule in full, with what each of its example pages shows: what a builder is pointed
    to when a measured fault comes back."""
    if rule is None:
        return ""
    lines = [f"{rule.rule} [{rule.source}, {rule.source_url}]"]
    lines += [f"- {e.name}, {e.url}: {e.shows}" for e in rule.examples]
    return "\n".join(lines)


def checklist(rules: Sequence[Rule], *, title: str = "") -> str:
    """The rules nothing measures, as questions a reviewer answers about the pictures of a
    page and the change."""
    asked = [rule for rule in rules if not measured(rule)]
    if not asked:
        return ""
    name = title or TITLES.get(asked[0].set, asked[0].set.capitalize())
    lines = [
        f"{name} checklist. Look at the pictures and the change and answer each with yes, no "
        "or cannot tell, naming the element for every no. A no is a finding only when you can "
        "point at it in a picture or in the diff; do not add opinions that are not on this list.",
    ]
    for rule in asked:
        firm = BASIS.get(rule.basis, rule.basis)
        lines.append(f"- [{rule.id}] {rule.rule} [{rule.source}{'; ' + firm if firm else ''}]")
    return "\n".join(lines)


def reviewer_text(sets: Sequence[str]) -> str:
    parts = [checklist(load(name)) for name in SETS if name in sets]
    return "\n\n".join(part for part in parts if part)
