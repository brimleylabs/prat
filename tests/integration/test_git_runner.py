"""The git runner: unchanged config is not rewritten, and a config race is retried."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from prat.git import runner
from prat.git.runner import Git

pytestmark = pytest.mark.integration


def test_config_set_skips_unchanged_values(repo_factory: Callable[..., Path]) -> None:
    repo = repo_factory("cfg")
    git = Git(repo)
    git.config_set("prat.test", "one")
    config = repo / ".git" / "config"
    before = config.stat().st_mtime_ns
    content = config.read_bytes()
    git.config_set("prat.test", "one")
    assert config.stat().st_mtime_ns == before and config.read_bytes() == content
    git.config_set("prat.test", "two")
    assert git.config_get("prat.test") == "two"
    git.config_set("prat.empty", "")
    git.config_set("prat.empty", "")
    assert git.run("config", "--local", "--get", "prat.empty").stdout == "\n"


def fake_runs(monkeypatch: pytest.MonkeyPatch, outcomes: list[tuple[int, str]]) -> list[list[str]]:
    calls: list[list[str]] = []

    def run_tree(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        code, stderr = outcomes[min(len(calls), len(outcomes)) - 1]
        return subprocess.CompletedProcess(argv, code, "ok\n" if code == 0 else "", stderr)

    monkeypatch.setattr(runner, "run_tree", run_tree)
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)
    return calls


def test_a_config_race_is_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    race = "fatal: unknown error occurred while reading the configuration files"
    calls = fake_runs(monkeypatch, [(128, race), (128, race), (0, "")])
    assert Git(tmp_path).out("status") == "ok"
    assert len(calls) == 3


def test_other_failures_are_not_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = fake_runs(monkeypatch, [(1, "error: pathspec 'x' did not match")])
    assert not Git(tmp_path).run("add", "x", check=False).ok
    assert len(calls) == 1


def test_a_persistent_race_still_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = fake_runs(monkeypatch, [(255, "error: could not lock config file .git/config")])
    with pytest.raises(runner.GitError, match="could not lock config file"):
        Git(tmp_path).run("config", "--local", "a.b", "c")
    assert len(calls) == runner.CONFIG_RETRIES + 1
