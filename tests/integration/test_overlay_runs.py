"""Runs under an installation overlay, and the prat overlay commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import add_card, git, make_engine
from prat import store
from prat.cli import app
from prat.config import Settings
from prat.db import Database, Project
from prat.journal import read_journal
from prat.overlay import overlay_path
from prat.projects import ProjectSpec

pytestmark = pytest.mark.integration

RULES = """
require_person = ["infra"]

[[deny_command]]
pattern = "make deploy*"
reason = "deploys go through the platform team"

[[deny_path]]
pattern = "infra/**"
reason = "owned by the platform team"

[[change_rule]]
paths = ["infra/**", "ops/**"]
category = "infra"
risk = "high"
"""


def write_overlay(home: Path) -> None:
    root = overlay_path(home)
    (root / "rules").mkdir(parents=True)
    (root / "overlay.toml").write_text("overlay_api = 1\n", encoding="utf-8")
    (root / "rules" / "platform.toml").write_text(RULES, encoding="utf-8")


async def test_a_run_follows_the_overlay(
    settings: Settings, db: Database, project: Project
) -> None:
    write_overlay(settings.home)
    script = [
        {"op": "bash", "command": "make deploy"},
        {"op": "write", "path": "infra/main.tf", "content": "# change\n"},
        {"op": "write", "path": "ops/runbook.md", "content": "# runbook\n"},
        {"op": "finish", "message": "done"},
    ]
    engine, runtime = make_engine(settings, db, script)
    card_id = add_card(db, project)
    result = await engine.run_card(card_id)

    with db.session() as session:
        run = store.latest_run(session, store.get_card(session, card_id))
        assert run is not None
        spec = ProjectSpec.model_validate(run.spec)
        prepare = next(s for s in run.steps if str(s.name) == "prepare")
        overlay_detail = prepare.detail["overlay"]
        journal = Path(run.journal_path or "")
    assert "infra/**" in spec.protected_paths
    assert spec.changes.require_person == ["infra"]
    assert "rules/platform.toml" in overlay_detail["loaded"]

    reasons = [
        e.data["reason"]
        for e in read_journal(journal)
        if e.kind == "policy" and not e.data["allowed"]
    ]
    assert any("platform team" in r and "installation overlay" in r for r in reasons)
    assert any("infra/main.tf" in r or "protected" in r for r in reasons)
    assert ("bash", "make deploy") not in runtime.executed
    # The ops change is classified by the overlay's rule and needs a person.
    assert result.card_status.value == "review"
    with db.session() as session:
        card = store.get_card(session, card_id)
        verify = next(s for s in card.runs[-1].steps if str(s.name) == "verify")
        changes = verify.detail["changes"]
    assert changes["categories"].get("infra") == "high"
    assert "infra" in changes["require_person"]
    files = git(Path(project.agent_path), "ls-tree", "-r", "--name-only", card.branch or "")
    assert "infra/main.tf" not in files and "ops/runbook.md" in files


def test_the_overlay_commands(tmp_path: Path, repo_factory: object) -> None:
    runner = CliRunner()
    home = tmp_path / "home"

    def cli(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, ["--home", str(home), *args], env={"COLUMNS": "200"})
        assert result.exit_code == code, result.output
        return result.output

    cli("init")
    assert "no overlay" in cli("overlay", "show")
    assert "overlay ready" in cli("overlay", "init")
    cli("overlay", "init")  # safe to run again
    root = overlay_path(home)
    assert "1 file(s) applied" in cli("overlay", "validate")

    (root / "rules" / "platform.toml").write_text(RULES, encoding="utf-8")
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "Deny deploys")
    shown = cli("overlay", "show")
    assert "denies command make deploy*" in shown and "needs a person for: infra" in shown
    log = cli("overlay", "log")
    assert "Deny deploys" in log and "Set up the overlay" in log

    sha = git(root, "rev-parse", "HEAD")
    assert "undone" in cli("overlay", "undo", sha)
    assert not (root / "rules" / "platform.toml").exists()
    assert "no overlay commit" in cli("overlay", "undo", "0" * 40, code=1)

    bad = '[[allow_command]]\npattern = "x"\n'
    (root / "rules" / "bad.toml").write_text(bad, encoding="utf-8")
    result = runner.invoke(app, ["--home", str(home), "overlay", "validate"])
    assert result.exit_code == 1
    assert "rules/bad.toml" in result.output and "allow_command" in result.output
