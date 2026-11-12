from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import DEFAULT_TOML, git
from prat.cli import app

pytestmark = pytest.mark.integration


@pytest.fixture
def cli(tmp_path: Path) -> Callable[..., object]:
    runner = CliRunner()
    home = tmp_path / "home"

    def invoke(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, ["--home", str(home), *args])
        assert result.exit_code == code, result.output
        return result.output

    return invoke


def test_full_cli_flow(
    cli: Callable[..., str], repo_factory: Callable[..., Path], tmp_path: Path
) -> None:
    repo = repo_factory("acme-api", project_toml=DEFAULT_TOML)
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            [
                {"op": "write", "path": "hello.py", "content": "def greet():\n    return 'hi'\n"},
                {"op": "bash", "command": "git push"},
                {"op": "finish", "message": "done"},
            ]
        ),
        encoding="utf-8",
    )

    assert "PRAT home ready" in cli("init")
    assert "added project" in cli("project", "add", str(repo))
    assert "acme-api" in cli("project", "list")
    assert '"autonomy": "medium"' in cli("project", "show", "acme-api")
    assert "card 1 created (draft)" in cli(
        "card", "add", "acme-api", "Add greet", "--goal", "Add hello.greet()"
    )
    out = cli("card", "run", "1", "--script", str(script))
    assert "review" in out
    shown = cli("card", "show", "1", "--journal")
    assert "hello.py" in shown
    assert "git push is not allowed" in shown
    out = cli("card", "approve", "1")
    assert "merged card/1-add-greet into main" in out
    assert "done" in cli("card", "list")
    assert not (repo / "hello.py").exists()
    assert (repo.parent / "acme-api-agent" / "hello.py").exists()
    assert git(repo, "branch", "--list") == "* main"


def test_failed_run_exits_non_zero(
    cli: Callable[..., str], repo_factory: Callable[..., Path], tmp_path: Path
) -> None:
    repo = repo_factory("acme-api", project_toml=DEFAULT_TOML)
    script = tmp_path / "empty.json"
    script.write_text('[{"op": "finish"}]', encoding="utf-8")
    cli("init")
    cli("project", "add", str(repo))
    cli("card", "add", "acme-api", "Do nothing", "--ready")
    out = cli("card", "run", "1", "--script", str(script), code=2)
    assert "failed" in out and "no changes" in out


def test_rework_and_cancel(
    cli: Callable[..., str], repo_factory: Callable[..., Path], tmp_path: Path
) -> None:
    repo = repo_factory("acme-api", project_toml=DEFAULT_TOML)
    script = tmp_path / "s.json"
    script.write_text('[{"op": "write", "path": "a.txt", "content": "a"}]', encoding="utf-8")
    cli("init")
    cli("project", "add", str(repo))
    cli("card", "add", "acme-api", "One", "--ready")
    cli("card", "run", "1", "--script", str(script))
    assert "rework" in cli("card", "rework", "1", "--feedback", "Rename a.txt")
    assert "cancelled" in cli("card", "cancel", "1")
    assert "nothing to cancel" in cli("card", "cancel", "1")


def test_commands_need_an_initialised_home(cli: Callable[..., str]) -> None:
    out = cli("project", "list", code=1)
    assert "prat init" in out


def test_errors_are_reported(
    cli: Callable[..., str],
    tmp_path: Path,
    repo_factory: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cli("init")
    assert "not a git checkout" in cli("project", "add", str(tmp_path), code=1)
    assert "no card 99" in cli("card", "show", "99", code=1)
    # The real runtime fails fast when its auth mode is not usable.
    assert "no key is set" in cli("card", "run", "1", "--runtime", "claude", code=1)
    assert "unknown runtime" in cli("card", "run", "1", "--runtime", "other", code=1)
    repo = repo_factory("acme-api", project_toml="name = \n")
    assert "invalid TOML" in cli("project", "add", str(repo), code=1)


def test_auth_status(cli: Callable[..., str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = cli("auth", "status", code=1)
    assert "mode:   api_key" in out and "usable: no" in out
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    out = cli("auth", "status")
    assert "usable: yes" in out and "test-key-not-real" not in out
    assert "model (builder): claude-sonnet-5-5" in out


def test_comment_then_rework_and_markup_is_shown_literally(
    cli: Callable[..., str], repo_factory: Callable[..., Path], tmp_path: Path
) -> None:
    repo = repo_factory("acme-api", project_toml=DEFAULT_TOML)
    script = tmp_path / "s.json"
    script.write_text('[{"op": "write", "path": "a.txt", "content": "a"}]', encoding="utf-8")
    cli("init")
    cli("project", "add", str(repo))
    cli("card", "add", "acme-api", "One [bold]", "--ready")
    cli("card", "run", "1", "--script", str(script))
    assert "add a comment" in cli("card", "rework", "1", code=1)
    assert "comment added" in cli("card", "comment", "1", "[met] rename a.txt")
    assert "rework" in cli("card", "rework", "1")
    shown = cli("card", "show", "1")
    assert "[met] rename a.txt" in shown and "One [bold]" in shown
