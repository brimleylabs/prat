"""Auth mode checks. No real credentials are used or read."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from prat.config import AuthMode, load_settings
from prat.runtime import auth
from prat.runtime.auth import AuthError, check_auth, runtime_env
from prat.runtime.claude import ClaudeRuntime

FAKE_KEY = "test-key-not-real"


@pytest.fixture(autouse=True)
def _no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_api_key_mode_is_the_default(tmp_path: Path) -> None:
    settings = load_settings(tmp_path)
    assert settings.auth_mode is AuthMode.API_KEY
    status = check_auth(settings)
    assert not status.usable and "ANTHROPIC_API_KEY" in status.detail
    with pytest.raises(AuthError):
        status.require()
    with pytest.raises(AuthError):
        ClaudeRuntime.from_settings(settings)


def test_api_key_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    settings = load_settings(tmp_path)
    status = check_auth(settings)
    assert status.usable and "environment variable" in status.detail
    assert FAKE_KEY not in status.detail
    assert runtime_env(settings) == {"ANTHROPIC_API_KEY": FAKE_KEY}
    runtime = ClaudeRuntime.from_settings(settings)
    assert runtime.config.env == {"ANTHROPIC_API_KEY": FAKE_KEY}


def test_api_key_from_settings_is_secret(tmp_path: Path) -> None:
    settings = load_settings(tmp_path, anthropic_api_key=FAKE_KEY)
    assert FAKE_KEY not in repr(settings) and FAKE_KEY not in str(settings.model_dump())
    status = check_auth(settings)
    assert status.usable and "anthropic_api_key" in status.detail and FAKE_KEY not in status.detail


def _fake_cli(tmp_path: Path, payload: str, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Pretend to be `claude auth status --json`, recording the environment."""
    cli = tmp_path / "claude-cli"
    cli.write_text("", encoding="utf-8")
    seen: list[Any] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append((argv, kwargs["env"]))
        return subprocess.CompletedProcess(argv, 0, stdout=payload, stderr="")

    monkeypatch.setattr(auth, "run_tree", fake_run)
    return [cli, seen]


def test_login_mode_signed_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    payload = '{"loggedIn": true, "authMethod": "claude.ai", "email": "person@example.invalid"}'
    cli, seen = _fake_cli(tmp_path, payload, monkeypatch)
    settings = load_settings(tmp_path, auth_mode="claude_login", claude_cli_path=cli)
    status = check_auth(settings)
    assert status.usable and status.login_kind == "claude.ai"
    assert "example.invalid" not in status.detail  # identity details are never repeated
    argv, env = seen[0]
    assert argv[1:] == ["auth", "status", "--json"]
    assert env["ANTHROPIC_API_KEY"] == ""  # the login is checked, not the stray key
    assert runtime_env(settings) == {"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""}


def test_login_mode_signed_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli, _ = _fake_cli(tmp_path, '{"loggedIn": false}', monkeypatch)
    settings = load_settings(tmp_path, auth_mode="claude_login", claude_cli_path=cli)
    status = check_auth(settings)
    assert not status.usable and "claude /login" in status.detail


def test_login_mode_garbage_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cli, _ = _fake_cli(tmp_path, "not json", monkeypatch)
    settings = load_settings(tmp_path, auth_mode="claude_login", claude_cli_path=cli)
    assert not check_auth(settings).usable


def test_login_mode_without_cli(tmp_path: Path) -> None:
    settings = load_settings(
        tmp_path, auth_mode="claude_login", claude_cli_path=tmp_path / "missing"
    )
    status = check_auth(settings)
    assert not status.usable and "no Claude Code executable" in status.detail


def test_bundled_cli_is_found(tmp_path: Path) -> None:
    cli = auth.find_claude_cli(load_settings(tmp_path))
    assert cli is None or cli.is_file()
    assert sys.executable  # keeps the import used on every platform
