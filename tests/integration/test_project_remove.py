"""Removing a project: its cards go, the person's checkout stays, knowledge is archived."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import DEFAULT_TOML
from prat.cli import app

pytestmark = pytest.mark.integration


def test_remove_a_project(tmp_path: Path, repo_factory: Callable[..., Path]) -> None:
    repo = repo_factory("gone", project_toml=DEFAULT_TOML.replace('"acme-api"', '"gone"'))
    home = tmp_path / "home"
    runner = CliRunner()

    def cli(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, ["--home", str(home), *args], env={"COLUMNS": "200"})
        assert result.exit_code == code, result.output
        return result.output

    cli("init")
    cli("project", "add", str(repo))
    cli("card", "add", "gone", "Something to do")
    cli("knowledge", "add", "gone", "command", "Run make test")
    clone = repo.parent / "gone-agent"
    clone.mkdir()

    cli("project", "remove", "gone", "--yes", "--delete-clone")

    assert "gone" not in cli("project", "list")
    assert repo.is_dir() and (repo / ".git").exists()  # the person's checkout is untouched
    assert not clone.exists()
    (archived,) = (home / "removed").iterdir()
    assert (archived / "gone" / "entries").is_dir()
    cli("project", "remove", "gone", "--yes", code=1)


def test_set_a_projects_autonomy_without_a_project_file(
    tmp_path: Path, repo_factory: Callable[..., Path]
) -> None:
    from prat import store
    from prat.config import load_settings
    from prat.db import Database
    from prat.engine.orchestrator import _spec_of

    repo = repo_factory("bare")  # no .prat/project.toml
    home = tmp_path / "home"
    runner = CliRunner()

    def cli(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, ["--home", str(home), *args], env={"COLUMNS": "200"})
        assert result.exit_code == code, result.output
        return result.output

    cli("init")
    cli("project", "add", str(repo))
    assert "autonomy medium" in cli("project", "set", "bare", "--autonomy", "medium")
    cli("project", "set", "bare", code=1)
    db = Database(load_settings(home).db_url)
    with db.session() as session:
        assert str(_spec_of(store.get_project(session, "bare")).autonomy) == "medium"
    db.dispose()
