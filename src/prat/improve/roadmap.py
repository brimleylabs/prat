"""The roadmap source: planned work in ``docs/ROADMAP.md``.

Each level-two heading is one item. Under it: an optional ``Effort:`` line,
a description, and an ``Acceptance criteria:`` list. A heading ending in
``(done)`` or ``(manual)`` is skipped. The file is read from the self clone's
default branch, never from a working tree an agent may have changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from prat.domain import Effort, FindingSource, Severity
from prat.git import Git
from prat.improve.findings import FindingDraft, finding_fingerprint

ROADMAP_REL = "docs/ROADMAP.md"
SKIP_MARKERS = ("(done)", "(manual)")
MAX_ITEMS = 100
MAX_CRITERIA = 8

_HEADING = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_EFFORT = re.compile(r"^effort:\s*(low|medium|high)\s*$", re.IGNORECASE)
_CRITERIA = re.compile(r"^acceptance criteria:\s*$", re.IGNORECASE)
_BULLET = re.compile(r"^[-*]\s+(.+)$")


@dataclass(frozen=True)
class RoadmapItem:
    title: str
    description: str
    acceptance_criteria: tuple[str, ...]
    effort: Effort
    position: int

    @property
    def fingerprint(self) -> str:
        return finding_fingerprint(FindingSource.ROADMAP, _key(self.title))

    def to_draft(self) -> FindingDraft:
        return FindingDraft(
            source=FindingSource.ROADMAP,
            fingerprint=self.fingerprint,
            title=self.title,
            problem=f"Planned in the roadmap: {self.title}.",
            proposal=self.description,
            acceptance_criteria=list(self.acceptance_criteria),
            severity=Severity.MEDIUM,
            effort=self.effort,
            evidence={"roadmap_position": self.position, "file": ROADMAP_REL},
        )


def _key(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


def parse_roadmap(text: str) -> list[RoadmapItem]:
    """Every open item in file order. Items without criteria get none here;
    the cycle adds the standard criteria to every card."""
    sections: list[tuple[str, list[str]]] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        heading = _HEADING.match(line)
        if heading and not line.startswith("###"):
            sections.append((heading.group(1).strip(), []))
        elif sections:
            sections[-1][1].append(line)

    items: list[RoadmapItem] = []
    for position, (title, body) in enumerate(sections, start=1):
        if any(title.casefold().endswith(marker) for marker in SKIP_MARKERS) or not title:
            continue
        effort = Effort.MEDIUM
        description: list[str] = []
        criteria: list[str] = []
        in_criteria = False
        for raw in body:
            line = raw.strip()
            if match := _EFFORT.match(line):
                effort = Effort(match.group(1).lower())
            elif _CRITERIA.match(line):
                in_criteria = True
            elif in_criteria and (bullet := _BULLET.match(line)):
                criteria.append(bullet.group(1).strip())
            elif in_criteria and line:
                # Text after the list continues the last criterion.
                if criteria:
                    criteria[-1] = f"{criteria[-1]} {line}"
            elif not in_criteria:
                description.append(line)
        items.append(
            RoadmapItem(
                title=title,
                description=re.sub(r"\n{3,}", "\n\n", "\n".join(description)).strip(),
                acceptance_criteria=tuple(criteria[:MAX_CRITERIA]),
                effort=effort,
                position=position,
            )
        )
        if len(items) >= MAX_ITEMS:
            break
    return items


def read_roadmap(clone: Path, branch: str) -> list[RoadmapItem]:
    """The roadmap as committed on ``branch``; empty when there is none."""
    shown = Git(clone, timeout=60).run("show", f"{branch}:{ROADMAP_REL}", check=False)
    return parse_roadmap(shown.stdout) if shown.ok else []
