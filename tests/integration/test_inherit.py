"""Inherited memories: PRAT's record of past runs becomes the crew's episodes, marked as
inherited, with the outcome's feeling and the person's feedback word for word."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import update

from conftest import add_card
from prat import inherit, store
from prat.db import Card, Database, Project, Run, RunStep
from prat.domain import CardStatus, CommentAuthor, RunStatus, StepName, StepStatus
from prat.memory import AgentMemory, briefing, new_episode, track_record

THEN = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def add_run(
    db: Database,
    card_id: int,
    outcome: RunStatus,
    *,
    message: str = "",
    feedback: str | None = None,
    reason: str | None = None,
    at: datetime = THEN,
) -> int:
    with db.session() as session:
        run = Run(card_id=card_id, status=RunStatus.QUEUED, runtime="fake", created_at=at)
        run.status = RunStatus.RUNNING
        run.status = outcome
        run.outcome = outcome
        run.feedback = feedback
        run.reason = reason
        run.finished_at = at
        session.add(run)
        session.flush()
        step = RunStep(
            run_id=run.id,
            name=StepName.EXECUTE,
            position=0,
            status=StepStatus.PENDING,
            detail={"message": message, "files_changed": 3},
        )
        step.status = StepStatus.RUNNING
        step.status = StepStatus.SUCCEEDED
        session.add(step)
        return run.id


def comment(db: Database, card_id: int, run_id: int, kind: str, data: dict[str, Any]) -> None:
    with db.session() as session:
        card = session.get(Card, card_id)
        assert card is not None
        author = CommentAuthor.REVIEWER if kind == "verdict" else CommentAuthor.SYSTEM
        store.add_comment(session, card, kind, author=author, kind=kind, run_id=run_id, data=data)


def set_status(db: Database, card_id: int, status: CardStatus) -> None:
    with db.session() as session:
        session.execute(update(Card).where(Card.id == card_id).values(status=status))


def history(db: Database, project: Project) -> tuple[int, int]:
    """One card landed first time; one sent back with feedback, then landed."""
    first = add_card(db, project, "Add tax to totals", ready=False)
    run = add_run(db, first, RunStatus.SUCCEEDED, message="Totals include tax now.")
    comment(db, first, run, "verdict", {"verdict": "met", "summary": "Tax is right."})
    comment(db, first, run, "lesson", {"title": "Money in cents", "trap": "floats round"})
    set_status(db, first, CardStatus.DONE)
    second = add_card(db, project, "Fix the rounding", ready=False)
    add_run(db, second, RunStatus.SUCCEEDED, message="Rounded at the end.")
    add_run(
        db,
        second,
        RunStatus.SUCCEEDED,
        message="Rounded each line.",
        feedback="Round per line, not at the end.",
        at=THEN + timedelta(hours=1),
    )
    set_status(db, second, CardStatus.DONE)
    return first, second


def test_the_lead_inherits_each_run_as_it_turned_out(
    db: Database, project: Project, tmp_path: Path
) -> None:
    first, second = history(db, project)
    home = tmp_path / "home"
    found = inherit.inherit(db, home)
    lead = found.leads[project.slug]
    episodes = {(e.card_id, e.run_id): e for e in AgentMemory(home, lead).episodes()}
    assert len(episodes) == 3
    assert all(e.inherited for e in episodes.values())
    landed = next(e for e in episodes.values() if e.card_id == first)
    assert landed.outcome == "landed" and landed.feeling.words == "proud"
    assert "Totals include tax now." in landed.what and "Changed 3 files" in landed.what
    assert landed.lesson == "Money in cents. floats round"
    assert landed.at == THEN.timestamp()
    sent_back, then_landed = sorted(
        (e for e in episodes.values() if e.card_id == second), key=lambda e: e.at
    )
    assert sent_back.outcome == "reworked"
    assert sent_back.feedback == "Round per line, not at the end."
    assert sent_back.feeling.words == "disappointed"
    assert then_landed.outcome == "landed" and then_landed.feeling.words == "satisfied"
    critic = AgentMemory(home, "critic").episodes()
    assert [e.what for e in critic] == ["Reviewed it and said met: Tax is right."]
    record = track_record(AgentMemory(home, lead))
    assert "2 cards landed (1 on the first review), 1 sent back" in record


def test_inheriting_again_adds_nothing_and_lived_runs_are_left_alone(
    db: Database, project: Project, tmp_path: Path
) -> None:
    first, _second = history(db, project)
    home = tmp_path / "home"
    lead = inherit.lead_key(db, project.slug, Path(project.checkout_path))
    with db.session() as session:
        run_id = session.query(Run.id).filter(Run.card_id == first).scalar()
    lived = new_episode(
        lead, project=project.slug, card_id=first, run_id=run_id, role="builder", title="mine"
    )
    AgentMemory(home, lead).save(lived)
    found = inherit.inherit(db, home)
    assert found.skipped == 1
    assert sum(found.added[lead].values()) == 2
    again = inherit.inherit(db, home)
    assert again.total == 0
    assert "Nothing new to inherit" in inherit.describe(home, again)


def test_a_dry_run_adds_nothing(db: Database, project: Project, tmp_path: Path) -> None:
    history(db, project)
    home = tmp_path / "home"
    found = inherit.inherit(db, home, dry_run=True)
    assert found.total == 4
    assert not (home / "agents").exists()


def test_the_briefing_says_inherited_memories_are_from_the_record(
    db: Database, project: Project, tmp_path: Path
) -> None:
    history(db, project)
    home = tmp_path / "home"
    lead = inherit.inherit(db, home).leads[project.slug]
    text = briefing(
        AgentMemory(home, lead), project=project.slug, card_id=None, query="rounding totals"
    )
    assert "(from the record, before you kept memories)" in text
    assert "You felt" not in text
    assert "Round per line, not at the end." in text
