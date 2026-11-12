"""Gates run agent-written code without PRAT's own settings and keys."""

from __future__ import annotations

import pytest

from prat.verification import gate_environment


def test_private_variables_are_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAT_DATABASE_URL", "mysql://PRAT:secret@127.0.0.1/PRAT")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "token")
    monkeypatch.setenv("PROJECT_SETTING", "kept")
    env = gate_environment({"APP_DB_URL": "mysql://app@127.0.0.1/shop/card-1"})
    assert "PRAT_DATABASE_URL" not in env
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["PROJECT_SETTING"] == "kept"
    assert env["APP_DB_URL"].endswith("card-1")
    assert "PATH" in env or "Path" in env
