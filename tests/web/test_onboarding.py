"""First run: the board's checklist and the starter project file on a project page."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import update

from conftest import DEFAULT_TOML, git
from prat.config import Settings
from prat.db import Card, Database, Project
from prat.domain import CardStatus
from prat.web.data import load_setup
from web_support import make_web

pytestmark = pytest.mark.integration


def steps(db: Database, signed_in: bool = True) -> dict[str, bool]:
    with db.session() as session:
        view = load_setup(session, signed_in=signed_in, sign_in_detail="no login found")
    return {s.key: s.done for s in view.steps}


def test_a_fresh_install_starts_at_the_top(db: Database) -> None:
    assert steps(db, signed_in=False) == {
        "sign-in": False,
        "project": False,
        "gates": False,
        "card": False,
        "approve": False,
    }
    with db.session() as session:
        view = load_setup(session, signed_in=False, sign_in_detail="no login found")
    assert view.next_key == "sign-in"
    assert view.steps[0].detail == "no login found"


def test_steps_tick_off_as_setup_happens(
    settings: Settings, db: Database, project_factory: Callable[..., Project]
) -> None:
    bare = project_factory("bare", project_toml=None)
    assert steps(db) == {
        "sign-in": True,
        "project": True,
        "gates": False,
        "card": False,
        "approve": False,
    }
    with make_web(settings, db, bare) as web:
        board = web.client.get("/board").text
        assert "Get started" in board
        assert "2 of 5 done" in board
        assert 'href="/projects/bare"' in board

        page = web.client.get("/projects/bare").text
        assert "Add a project file" in page
        assert "No gates yet" in page
        assert 'id="suggested-file"' in page

        card_id = web.add_card()
        assert steps(db)["card"]
        with db.session() as session:
            session.execute(update(Card).where(Card.id == card_id).values(status=CardStatus.DONE))
        assert steps(db)["approve"]


def test_a_project_file_committed_in_prats_clone_counts(
    db: Database, project_factory: Callable[..., Project]
) -> None:
    """An approved setup card commits the project file to PRAT's clone, not the person's
    checkout; the checklist must see it there."""
    bare = project_factory("bare", project_toml=None)
    assert not steps(db)["gates"]
    agent = Path(bare.agent_path)
    git(agent.parent, "clone", "-q", bare.checkout_path, str(agent))
    (agent / ".prat").mkdir()
    (agent / ".prat" / "project.toml").write_text(
        DEFAULT_TOML.replace('"acme-api"', '"bare"'), encoding="utf-8"
    )
    git(agent, "add", ".prat")
    git(agent, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "Add file")
    assert steps(db)["gates"]


def test_the_checklist_goes_away_when_done(
    settings: Settings, db: Database, project: Project
) -> None:
    with make_web(settings, db, project) as web:
        card_id = web.add_card()
        with db.session() as session:
            session.execute(update(Card).where(Card.id == card_id).values(status=CardStatus.DONE))
        board = web.client.get("/board").text
        assert "Get started" not in board
        page = web.client.get(f"/projects/{project.slug}").text
        assert "Add a project file" not in page
