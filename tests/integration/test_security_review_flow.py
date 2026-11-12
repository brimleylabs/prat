"""The security reviewer in the engine: always on for PRAT's own repository."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import WRITE_GREET, add_card, git, make_engine, make_repo, project_toml
from prat import store
from prat.config import Settings
from prat.db import Database, Project
from prat.domain import CardStatus, StepName, Verdict
from prat.engine import (
    ApproveError,
    FakeReviewer,
    FakeSecurityReviewer,
    SecurityFinding,
    SecurityVerdict,
)
from prat.improve.selfproject import setup_self_project

pytestmark = pytest.mark.integration

CONCERN = FakeSecurityReviewer(
    SecurityVerdict.CONCERN,
    [SecurityFinding("the push rule for agents was removed", "src/prat/policy/engine.py")],
)


@pytest.fixture
def self_project(settings: Settings, db: Database, tmp_path: Path) -> Project:
    source = make_repo(
        tmp_path / "src",
        "prat",
        project_toml=project_toml().replace('"acme-api"', '"prat"'),
        files={"src/prat/__init__.py": '"""PRAT."""\n'},
    )
    bare = tmp_path / "remote" / "prat.git"
    bare.parent.mkdir(parents=True)
    git(bare.parent, "clone", "-q", "--bare", str(source), str(bare))
    git(source, "remote", "add", "origin", str(bare))
    setup_self_project(db, settings, running_from=source / "src/prat/__init__.py")
    with db.session() as session:
        project = session.scalars(select(Project).where(Project.is_self.is_(True))).one()
        session.expunge(project)
        return project


def critique(db: Database, card_id: int) -> dict[str, object]:
    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None
        return dict(next(s for s in run.steps if s.name == StepName.CRITIQUE).detail)


def comment_kinds(db: Database, card_id: int) -> list[str]:
    with db.session() as session:
        return [c.kind for c in store.get_card(session, card_id).comments]


async def test_the_self_project_always_gets_a_security_review(
    settings: Settings, db: Database, self_project: Project
) -> None:
    security = FakeSecurityReviewer()
    engine, _ = make_engine(settings, db, WRITE_GREET, security_reviewer=security)
    card_id = add_card(db, self_project)
    result = await engine.run_card(card_id)
    assert result.card_status is CardStatus.REVIEW
    (request,) = security.requests
    assert request.model == "claude-sonnet-5-5"
    detail = critique(db, card_id)
    assert detail["security_required"] is True
    security_detail = detail["security"]
    assert isinstance(security_detail, dict) and security_detail["verdict"] == "pass"
    assert "security" in comment_kinds(db, card_id)
    merged = await engine.approve(card_id, automatic=True)
    assert merged.commit


async def test_its_tools_are_read_only(
    settings: Settings, db: Database, self_project: Project
) -> None:
    from prat.policy import ToolCall

    security = FakeSecurityReviewer()
    engine, _ = make_engine(settings, db, WRITE_GREET, security_reviewer=security)
    card_id = add_card(db, self_project)
    await engine.run_card(card_id)
    (request,) = security.requests
    assert request.policy is not None and request.workdir
    target = str(Path(request.workdir) / "x.py")
    assert not request.policy(ToolCall.write(target, "x")).allowed
    assert not request.policy(ToolCall.bash("touch x.py")).allowed


async def test_a_concern_sends_the_card_back_with_the_findings(
    settings: Settings, db: Database, self_project: Project
) -> None:
    engine, _ = make_engine(settings, db, WRITE_GREET, security_reviewer=CONCERN)
    card_id = add_card(db, self_project)
    result = await engine.run_card(card_id)
    # The criteria reviewer said met; the reviewers disagree, so the card goes back.
    assert result.card_status is CardStatus.REWORK
    with db.session() as session:
        card = store.get_card(session, card_id)
        assert card.status_reason == "security review raised a concern: sent back"
        fix = next(c for c in card.comments if c.kind == "fix_list")
        assert "the push rule for agents was removed" in fix.body
        assert fix.data["verdict"] == "security_concern"
    with pytest.raises(ApproveError, match="only cards in review"):
        await engine.approve(card_id, automatic=True)


async def test_a_criteria_rejection_skips_the_security_review(
    settings: Settings, db: Database, self_project: Project
) -> None:
    security = FakeSecurityReviewer()
    engine, _ = make_engine(
        settings,
        db,
        WRITE_GREET,
        reviewer=FakeReviewer(Verdict.NOT_MET, fix_list=["add tests"]),
        security_reviewer=security,
    )
    card_id = add_card(db, self_project)
    assert (await engine.run_card(card_id)).card_status is CardStatus.REWORK
    assert security.requests == []
    assert "security" not in critique(db, card_id)


async def test_without_a_security_reviewer_the_self_project_fails_closed(
    settings: Settings, db: Database, self_project: Project
) -> None:
    engine, _ = make_engine(settings, db, WRITE_GREET)
    engine.security_reviewer = None
    card_id = add_card(db, self_project)
    assert (await engine.run_card(card_id)).card_status is CardStatus.REWORK
    security_detail = critique(db, card_id)["security"]
    assert isinstance(security_detail, dict)
    assert security_detail["verdict"] == "concern" and security_detail["parsed"] is False


async def test_other_projects_opt_in_through_the_project_file(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    plain = project_factory("plain")
    reviewed = project_factory(
        "reviewed",
        project_toml=project_toml(extra='[review]\nreviewers = ["criteria", "security"]'),
    )
    security = FakeSecurityReviewer()
    engine, _ = make_engine(settings, db, WRITE_GREET, security_reviewer=security)
    first = add_card(db, plain)
    await engine.run_card(first)
    assert security.requests == []
    assert critique(db, first)["security_required"] is False
    # Without a required security review, automatic approval needs only the met verdict.
    await engine.approve(first, automatic=True)

    second = add_card(db, reviewed)
    await engine.run_card(second)
    assert len(security.requests) == 1
    assert critique(db, second)["security_required"] is True


async def test_automatic_approval_needs_the_security_pass_for_this_commit(
    settings: Settings, db: Database, self_project: Project
) -> None:
    engine, _ = make_engine(settings, db, WRITE_GREET)
    card_id = add_card(db, self_project)
    await engine.run_card(card_id)
    # Remove the security result as if it had never run: automatic approval refuses.
    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None
        step = next(s for s in run.steps if s.name == StepName.CRITIQUE)
        step.detail = {k: v for k, v in step.detail.items() if k != "security"}
    with pytest.raises(ApproveError, match="needs a security review pass"):
        await engine.approve(card_id, automatic=True)
    # A person may still approve it in review mode.
    await engine.approve(card_id)
