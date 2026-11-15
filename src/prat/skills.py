"""Skills that earn their place: candidates, promotion, and a scorecard per skill.

An agent proposes a skill during a card (the ``propose_skill`` tool); it waits as a candidate in
``$PRAT_HOME/skills/<project>/candidates`` until a person promotes it (it then joins the
project's loadout, prat.crew) or rejects it.

Every run records the skills its agents loaded (``skills_used`` on the build step). The
scorecard joins that with how cards turned out: how often a skill is loaded, the share of the
cards that used it which landed on their first review, against the cards that did not use it,
and when it was last loaded. A project skill nobody has loaded in a month, or whose cards do
clearly worse, is flagged for retirement; one whose cards do clearly better is marked as
helping. The library stays small and good instead of growing forever.
"""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from prat import crew
from prat.db import Card, Database, Project, Run, RunStep, as_utc
from prat.domain import CardStatus, StepName

RETIRE_DAYS = 30
MIN_CARDS = 3
MARGIN = 0.15


class SkillError(ValueError):
    pass


@dataclass
class Candidate:
    name: str
    path: Path
    description: str
    phase: str
    source: str


def candidates(home: Path, slug: str) -> list[Candidate]:
    folder = crew.candidates_dir(home, slug)
    found = []
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        meta = crew.skill_meta(path)
        found.append(
            Candidate(
                name=path.stem,
                path=path,
                description=meta.get("description", ""),
                phase=meta.get("phase", "any"),
                source=meta.get("source", ""),
            )
        )
    return found


def _name(name: str) -> str:
    clean = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    if not clean:
        raise SkillError("a skill needs a name")
    return clean


def promote(home: Path, slug: str, name: str) -> Path:
    name = _name(name)
    source = crew.candidates_dir(home, slug) / f"{name}.md"
    if not source.is_file():
        raise SkillError(f"no candidate {name!r} for {slug}")
    target = crew.project_skills(home, slug) / name
    if target.exists() or name in crew.available_skills(home, slug):
        raise SkillError(f"a skill named {name!r} already exists")
    target.mkdir(parents=True)
    shutil.move(str(source), str(target / "SKILL.md"))
    return target


def reject(home: Path, slug: str, name: str) -> None:
    name = _name(name)
    source = crew.candidates_dir(home, slug) / f"{name}.md"
    if not source.is_file():
        raise SkillError(f"no candidate {name!r} for {slug}")
    rejected = home / "skills" / slug / "rejected"
    rejected.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(rejected / f"{name}.{int(time.time())}.md"))


def retire(home: Path, slug: str, name: str) -> None:
    """Remove a project skill from the loadout (kept under retired/)."""
    name = _name(name)
    source = crew.project_skills(home, slug) / name
    if not source.is_dir():
        raise SkillError(f"{name!r} is not one of {slug}'s own skills")
    retired = home / "skills" / slug / "retired"
    retired.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(retired / f"{name}.{int(time.time())}"))


@dataclass
class Scorecard:
    name: str
    loads: int = 0
    cards: int = 0
    first_review: int = 0
    finished: int = 0
    last_loaded: float | None = None
    baseline: float | None = None
    tier: str = "stack"

    @property
    def first_review_rate(self) -> float | None:
        return self.first_review / self.finished if self.finished else None

    @property
    def advice(self) -> str:
        now = time.time()
        if self.tier == "project" and (
            self.last_loaded is None or now - self.last_loaded > RETIRE_DAYS * 86400
        ):
            return "retire? not loaded in a month"
        rate = self.first_review_rate
        if rate is None or self.baseline is None or self.finished < MIN_CARDS:
            return ""
        if rate >= self.baseline + MARGIN:
            return "helps"
        if rate <= self.baseline - MARGIN:
            return "retire? its cards do worse"
        return ""


def scorecards(db: Database, home: Path, slug: str) -> dict[str, Scorecard]:
    """Each skill's record on this project's finished cards."""
    with db.session() as session:
        project = session.scalar(select(Project).where(Project.slug == slug))
        if project is None:
            return {}
        rows = session.execute(
            select(Card.id, Card.status, RunStep.detail, Run.started_at)
            .join(Run, Run.card_id == Card.id)
            .join(RunStep, RunStep.run_id == Run.id)
            .where(Card.project_id == project.id, RunStep.name == StepName.EXECUTE)
        ).all()
        runs_per_card: dict[int, int] = {}
        for card_id, *_rest in rows:
            runs_per_card[card_id] = runs_per_card.get(card_id, 0) + 1
    used_by_card: dict[int, set[str]] = {}
    status_of: dict[int, CardStatus] = {}
    cards: dict[str, Scorecard] = {}
    for card_id, status, detail, started in rows:
        status_of[card_id] = CardStatus(status)
        names = {str(s).split(" (")[0] for s in (detail or {}).get("skills_used") or []}
        used_by_card.setdefault(card_id, set()).update(names)
        moment = as_utc(started) if started is not None else None
        stamp = moment.timestamp() if moment is not None else None
        for name in names:
            card = cards.setdefault(name, Scorecard(name))
            card.loads += 1
            if stamp is not None and (card.last_loaded is None or stamp > card.last_loaded):
                card.last_loaded = stamp
    finished = {c for c, s in status_of.items() if s in (CardStatus.DONE, CardStatus.CANCELLED)}
    first = {c for c in finished if status_of[c] == CardStatus.DONE and runs_per_card.get(c) == 1}
    for name, card in cards.items():
        mine = {c for c, used in used_by_card.items() if name in used}
        card.cards = len(mine)
        card.finished = len(mine & finished)
        card.first_review = len(mine & first)
        others = finished - mine
        card.baseline = len(others & first) / len(others) if others else None
    for name, path in crew.available_skills(home, slug).items():
        tier = crew.skill_meta(path / "SKILL.md").get("tier", "project")
        cards.setdefault(name, Scorecard(name)).tier = tier
    return cards
