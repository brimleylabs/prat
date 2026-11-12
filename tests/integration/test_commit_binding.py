"""Approval is bound to the commit the gates and the reviewer looked at."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import WRITE_GREET, add_card, git, make_engine, project_toml
from prat import store
from prat.config import Settings
from prat.db import Database, Project, RunStep
from prat.domain import CardStatus, StepName, Verdict
from prat.engine import ApproveError, FakeReviewer

pytestmark = pytest.mark.integration

AUTH_SCRIPT = [
    {"op": "write", "path": "app/auth/session.py", "content": "TIMEOUT = 30\n"},
    {"op": "finish", "message": "session timeout"},
]


def step_detail(db: Database, card_id: int, name: StepName) -> dict[str, object]:
    with db.session() as session:
        card = store.get_card(session, card_id)
        run = store.latest_run(session, card)
        assert run is not None
        step = next(s for s in run.steps if s.name == name)
        return dict(step.detail)


def card_status(db: Database, card_id: int) -> CardStatus:
    with db.session() as session:
        return store.get_card(session, card_id).status


def branch_of(db: Database, card_id: int) -> str:
    with db.session() as session:
        branch = store.get_card(session, card_id).branch
        assert branch
        return branch


def sneak_commit(agent: Path, branch: str) -> str:
    """Move the card branch on by one commit without running the card again."""
    tree = git(agent, "rev-parse", f"{branch}^{{tree}}")
    commit = git(agent, "commit-tree", tree, "-p", branch, "-m", "slipped in after review")
    git(agent, "update-ref", f"refs/heads/{branch}", commit)
    return commit


async def test_evidence_is_recorded_for_the_commit_it_was_computed_for(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    reviewer = FakeReviewer()
    engine, _ = make_engine(settings, db, WRITE_GREET, reviewer=reviewer)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW

    head = git(Path(project.agent_path), "rev-parse", branch_of(db, card_id))
    verify = step_detail(db, card_id, StepName.VERIFY)
    critique = step_detail(db, card_id, StepName.CRITIQUE)
    assert verify["head_commit"] == head
    assert critique["head_commit"] == head
    changes = verify["changes"]
    assert isinstance(changes, dict)
    assert changes["head"] == head
    assert list(changes["categories"]) == ["code"]

    (request,) = reviewer.requests
    assert request.head_commit == head
    assert request.changes == changes


async def test_the_reviewer_sees_the_classification(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    reviewer = FakeReviewer()
    engine, _ = make_engine(settings, db, AUTH_SCRIPT, reviewer=reviewer)
    await engine.run_card(card_id)
    (request,) = reviewer.requests
    assert request.changes is not None
    assert "security" in request.changes["categories"]


async def test_a_commit_after_review_blocks_approval(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    await engine.run_card(card_id)
    agent = Path(project.agent_path)
    sneak_commit(agent, branch_of(db, card_id))
    default_before = git(agent, "rev-parse", "main")

    for automatic in (False, True):
        with pytest.raises(ApproveError, match="was verified at"):
            await engine.approve(card_id, automatic=automatic)
    assert card_status(db, card_id) is CardStatus.REVIEW
    assert git(agent, "rev-parse", "main") == default_before


async def test_automatic_approval_with_a_met_verdict_merges(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    await engine.run_card(card_id)
    merged = await engine.approve(card_id, automatic=True)
    assert card_status(db, card_id) is CardStatus.DONE
    assert git(Path(project.agent_path), "rev-parse", "main") == merged.commit
    with db.session() as session:
        events = store.events_for_card(session, store.get_card(session, card_id))
        merged_event = next(e for e in events if e.kind == "card.merged")
        assert merged_event.data["automatic"] is True


async def test_a_partial_verdict_blocks_automatic_approval_only(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, WRITE_GREET, reviewer=FakeReviewer(Verdict.PARTIAL))
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW
    with pytest.raises(ApproveError, match="needs a 'met' verdict"):
        await engine.approve(card_id, automatic=True)
    assert card_status(db, card_id) is CardStatus.REVIEW
    await engine.approve(card_id)
    assert card_status(db, card_id) is CardStatus.DONE


async def test_categories_reserved_for_a_person_block_automatic_approval(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    project = project_factory(
        project_toml=project_toml(extra='[changes]\nrequire_person = ["security"]')
    )
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, AUTH_SCRIPT)
    await engine.run_card(card_id)
    changes = step_detail(db, card_id, StepName.VERIFY)["changes"]
    assert isinstance(changes, dict) and changes["require_person"] == ["security"]
    with pytest.raises(ApproveError, match="needs a person's approval: security"):
        await engine.approve(card_id, automatic=True)
    await engine.approve(card_id)
    assert card_status(db, card_id) is CardStatus.DONE


async def test_a_run_without_recorded_evidence_cannot_be_approved(
    settings: Settings, db: Database, project: Project
) -> None:
    card_id = add_card(db, project)
    engine, _ = make_engine(settings, db, WRITE_GREET)
    await engine.run_card(card_id)
    with db.session() as session:
        card = store.get_card(session, card_id)
        run = store.latest_run(session, card)
        assert run is not None
        verify: RunStep = next(s for s in run.steps if s.name == StepName.VERIFY)
        verify.detail = {k: v for k, v in verify.detail.items() if k != "head_commit"}
    with pytest.raises(ApproveError, match="no verification evidence"):
        await engine.approve(card_id)
    assert card_status(db, card_id) is CardStatus.REVIEW
