"""Approved work reaches a checkout PRAT cloned itself, and never a person's own clone."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import git
from prat.engine.orchestrator import _follow_checkout

pytestmark = pytest.mark.integration


def identify(repo: Path) -> None:
    git(repo, "config", "user.name", "Test Person")
    git(repo, "config", "user.email", "person@example.invalid")


def clones(tmp_path: Path, under: Path) -> tuple[Path, Path]:
    checkout = under / "shop"
    checkout.mkdir(parents=True)
    git(checkout, "init", "-q", "-b", "main")
    identify(checkout)
    (checkout / "a.txt").write_text("a\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "first")
    agent = tmp_path / "shop-agent"
    git(tmp_path, "clone", "-q", str(checkout), str(agent))
    identify(agent)
    (agent / ".prat").mkdir()
    (agent / ".prat" / "setup.sh").write_text("true\n")
    git(agent, "add", "-A")
    git(agent, "commit", "-q", "-m", "approved")
    return checkout, agent


def test_a_checkout_prat_cloned_gets_the_approved_work(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    checkout, agent = clones(tmp_path, projects)
    assert _follow_checkout(checkout, agent, "main", projects) == "updated"
    assert (checkout / ".prat" / "setup.sh").exists()


def test_a_persons_own_clone_is_never_touched(tmp_path: Path) -> None:
    checkout, agent = clones(tmp_path, tmp_path / "mine")
    assert _follow_checkout(checkout, agent, "main", tmp_path / "projects") == "not PRAT's"
    assert not (checkout / ".prat").exists()


def test_local_changes_in_the_checkout_are_left_alone(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    checkout, agent = clones(tmp_path, projects)
    (checkout / "a.txt").write_text("edited\n")
    assert _follow_checkout(checkout, agent, "main", projects).startswith("skipped")
    assert not (checkout / ".prat").exists()
