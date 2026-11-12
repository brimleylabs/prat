"""Self-improvement that targets the overlay: PRAT itself never changes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from conftest import add_card, git, make_engine
from prat import store
from prat.config import Settings
from prat.db import Database, Project
from prat.db.models import ImproveEvent
from prat.domain import CardStatus
from prat.improve.landing import Outcome, OverlayProver, land_self_card
from prat.improve.selfproject import load_self_info, overlay_clone_path, setup_self_project
from prat.improve.settings import load_improve_settings, update_improve_settings
from prat.overlay import load_overlay, overlay_path
from prat.projects import ProjectSpec

pytestmark = pytest.mark.integration

DENY_DEPLOYS = (
    '[[deny_command]]\npattern = "make deploy*"\nreason = "deploys go through the platform team"\n'
)


@pytest.fixture
def overlay_ready(settings: Settings, db: Database) -> Path:
    with db.session() as session:
        update_improve_settings(session, enabled=True)
    setup_self_project(db, settings, actor="test")
    return overlay_path(settings.home)


def self_project(db: Database) -> Project:
    with db.session() as session:
        project = session.scalars(select(Project).where(Project.is_self.is_(True))).one()
        session.expunge(project)
        return project


def kinds(db: Database, card_id: int) -> list[str]:
    with db.session() as session:
        query = select(ImproveEvent.kind).where(ImproveEvent.card_id == card_id)
        return list(session.scalars(query.order_by(ImproveEvent.id)))


def write_rule(content: str) -> list[dict[str, Any]]:
    return [
        {"op": "write", "path": "rules/deploys.toml", "content": content},
        {"op": "finish", "message": "added a rule"},
    ]


def test_the_default_target_is_the_overlay(settings: Settings, db: Database) -> None:
    with db.session() as session:
        assert load_improve_settings(session).target == "overlay"
    result = setup_self_project(db, settings, actor="test")
    live = overlay_path(settings.home)
    assert result.path == overlay_clone_path(settings).resolve()
    assert (live / ".prat" / "project.toml").is_file()
    assert git(live, "status", "--porcelain") == ""
    with db.session() as session:
        info = load_self_info(session)
    assert info is not None and info.is_overlay and info.push_available
    spec = ProjectSpec.model_validate(self_project(db).config)
    assert spec.verify.gates == ["test"] and "overlay.toml" in spec.protected_paths
    # The overlay itself still loads cleanly with its project file in it.
    assert load_overlay(live).disabled == []


async def test_a_proven_overlay_change_lands_in_the_live_overlay(
    settings: Settings, db: Database, overlay_ready: Path
) -> None:
    engine, _ = make_engine(settings, db, write_rule(DENY_DEPLOYS))
    card_id = add_card(db, self_project(db))
    run = await engine.run_card(card_id)
    assert run.card_status is CardStatus.REVIEW, run.reason
    with db.session() as session:
        verify = next(
            s for s in store.get_card(session, card_id).runs[-1].steps if str(s.name) == "verify"
        )
        assert all(g["passed"] for g in verify.detail["gates"])

    result = await land_self_card(db, settings, engine, card_id)
    assert result.outcome is Outcome.LANDED, result.message
    assert "applied to the overlay" in result.message
    events = kinds(db, card_id)
    assert "trial_passed" in events and "push_ok" in events and "card_landed" in events

    live = overlay_ready
    assert (live / "rules" / "deploys.toml").is_file()
    assert git(live, "status", "--porcelain") == ""
    found = load_overlay(live)
    assert [d.pattern for d in found.denied_commands] == ["make deploy*"]


async def test_an_invalid_overlay_change_never_lands(
    settings: Settings, db: Database, overlay_ready: Path
) -> None:
    clone = overlay_clone_path(settings)
    base = git(clone, "rev-parse", "HEAD")
    git(clone, "checkout", "-q", "-b", "bad")
    (clone / "rules" / "bad.toml").write_text(
        '[[allow_command]]\npattern = "x"\n', encoding="utf-8"
    )
    git(clone, "add", "--all")
    git(clone, "commit", "-q", "-m", "bad")
    bad = git(clone, "rev-parse", "HEAD")
    git(clone, "checkout", "-q", "main")
    proof = OverlayProver(clone).prove(base, bad)
    assert not proof.passed
    assert "allow_command" in proof.steps[0].message
    good = OverlayProver(clone).prove(base, base)
    assert good.passed, good.steps[0].message


async def test_uncommitted_edits_in_the_live_overlay_hold_landing(
    settings: Settings, db: Database, overlay_ready: Path
) -> None:
    engine, _ = make_engine(settings, db, write_rule(DENY_DEPLOYS))
    card_id = add_card(db, self_project(db))
    await engine.run_card(card_id)
    (overlay_ready / "prompts" / "builder.md").write_text("my own edit\n", encoding="utf-8")
    before = git(overlay_ready, "rev-parse", "HEAD")
    result = await land_self_card(db, settings, engine, card_id)
    assert result.outcome is Outcome.MERGED
    assert "uncommitted" in result.message
    assert git(overlay_ready, "rev-parse", "HEAD") == before
    assert not (overlay_ready / "rules" / "deploys.toml").exists()
    assert (overlay_ready / "prompts" / "builder.md").read_text(encoding="utf-8") == "my own edit\n"


async def test_core_setup_still_works_explicitly(settings: Settings, db: Database) -> None:
    with pytest.raises(Exception, match=r"not running from a git checkout|repository"):
        setup_self_project(db, settings, target="core", running_from=Path("/nowhere/prat.py"))


def test_research_aims_at_the_overlay() -> None:
    from prat.improve.research import research_prompt

    prompt = research_prompt(
        architecture="arch", roadmap="road", known_titles=[], max_proposals=2, overlay=True
    )
    assert "through its overlay, not by changing PRAT" in prompt
    assert "docs/ARCHITECTURE.md" not in prompt
    core = research_prompt(architecture="arch", roadmap="road", known_titles=[], max_proposals=2)
    assert "docs/ARCHITECTURE.md" in core and "overlay" not in core
