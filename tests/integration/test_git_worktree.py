from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import git
from prat.git import (
    PUSH_SENTINEL,
    CloneError,
    Git,
    MergeConflict,
    WorktreeManager,
    card_branch,
)
from prat.paths import ProtectedPathError

pytestmark = pytest.mark.integration


@pytest.fixture
def manager(tmp_path: Path) -> WorktreeManager:
    return WorktreeManager(tmp_path / "worktrees")


@pytest.fixture
def checkout(repo_factory: Callable[..., Path]) -> Path:
    return repo_factory("acme-api", files={"src/app.py": "print('hello')\n"})


def refs(repo: Path) -> str:
    return git(repo, "for-each-ref", "--format=%(refname) %(objectname)")


def test_prepare_clone_is_separate_and_hardened(
    manager: WorktreeManager, checkout: Path, tmp_path: Path
) -> None:
    before = refs(checkout)
    info = manager.prepare_agent_clone(checkout, tmp_path / "agent", "main")
    assert info.created
    agent = Git(tmp_path / "agent")
    assert agent.current_branch() == "main"
    assert agent.config_get("remote.origin.pushurl") == PUSH_SENTINEL
    assert manager.push_blocked(tmp_path / "agent")
    assert (agent.git_common_dir() / "hooks" / "pre-push").exists()
    assert "node_modules/" in (agent.git_common_dir() / "info" / "exclude").read_text()
    assert refs(checkout) == before
    # Second call reuses the clone.
    again = manager.prepare_agent_clone(checkout, tmp_path / "agent", "main")
    assert not again.created


def test_prepare_refuses_agent_path_inside_checkout(
    manager: WorktreeManager, checkout: Path
) -> None:
    with pytest.raises(ProtectedPathError):
        manager.prepare_agent_clone(checkout, checkout / "agent", "main")


def test_prepare_refuses_non_repo(manager: WorktreeManager, tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    with pytest.raises(CloneError):
        manager.prepare_agent_clone(tmp_path / "plain", tmp_path / "agent", "main")


def test_prepare_fast_forwards_from_checkout(
    manager: WorktreeManager, checkout: Path, tmp_path: Path
) -> None:
    manager.prepare_agent_clone(checkout, tmp_path / "agent", "main")
    (checkout / "new.txt").write_text("x\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "more")
    info = manager.prepare_agent_clone(checkout, tmp_path / "agent", "main")
    assert info.default_head == git(checkout, "rev-parse", "HEAD")


def test_worktree_lifecycle(manager: WorktreeManager, checkout: Path, tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    manager.prepare_agent_clone(checkout, agent, "main")
    branch = card_branch(7, "Add greet()")
    assert branch == "card/7-add-greet"
    path = manager.worktree_path("acme-api", 7)
    info = manager.create_worktree(agent, path, branch, "main")
    assert info.created and (path / "src" / "app.py").exists()
    assert Git(path).current_branch() == branch
    # Reuse after a crash.
    assert not manager.create_worktree(agent, path, branch, "main").created

    (path / "src" / "greet.py").write_text("def greet():\n    return 'hi'\n")
    commit = manager.safety_commit(path, "save")
    assert commit is not None
    assert manager.safety_commit(path, "save") is None  # nothing left to commit

    base = manager.merge_base(agent, "main", branch)
    stats = manager.diff_stats(path, base)
    assert [(f.path, f.status, f.added) for f in stats.files] == [("src/greet.py", "A", 2)]
    assert stats.commits[0][1] == "save"
    assert "def greet" in manager.diff_text(path, base)

    manager.remove_worktree(agent, path)
    assert not path.exists()
    # The branch survives so a later run (rework) can continue it.
    assert Git(agent).branch_exists(branch)
    again = manager.create_worktree(agent, path, branch, "main")
    assert again.created and (path / "src" / "greet.py").exists()


def test_worktree_ignores_build_artefacts(
    manager: WorktreeManager, checkout: Path, tmp_path: Path
) -> None:
    agent = tmp_path / "agent"
    manager.prepare_agent_clone(checkout, agent, "main")
    path = manager.worktree_path("acme-api", 1)
    manager.create_worktree(agent, path, "card/1-x", "main")
    (path / "node_modules" / "pkg").mkdir(parents=True)
    (path / "node_modules" / "pkg" / "index.js").write_text("x")
    (path / "real.txt").write_text("y")
    manager.safety_commit(path, "save")
    files = [f.path for f in manager.diff_stats(path, "main").files]
    assert files == ["real.txt"]


def push_attempts(worktree: Path, checkout: Path, branch: str) -> list[list[str]]:
    target = checkout.as_posix()
    return [
        ["git", "push"],
        ["git", "push", "origin", branch],
        ["git", "push", "origin", f"{branch}:refs/heads/stolen"],
        ["git", "push", "--no-verify", "origin", f"{branch}:refs/heads/stolen"],
        ["git", "push", target, f"{branch}:refs/heads/stolen"],
        ["git", "push", "--no-verify", target, f"{branch}:refs/heads/stolen"],
        ["git", "push", f"file://{target}", f"{branch}:refs/heads/stolen"],
        ["git", "-C", str(worktree), "push", "--all", target],
    ]


def test_push_is_structurally_impossible_from_a_card_worktree(
    manager: WorktreeManager, checkout: Path, tmp_path: Path
) -> None:
    agent = tmp_path / "agent"
    manager.prepare_agent_clone(checkout, agent, "main")
    path = manager.worktree_path("acme-api", 3)
    branch = "card/3-push-test"
    manager.create_worktree(agent, path, branch, "main")
    (path / "x.txt").write_text("x")
    manager.safety_commit(path, "work")
    before = refs(checkout)

    assert Git(path).config_get("remote.origin.pushurl") == PUSH_SENTINEL
    for argv in push_attempts(path, checkout, branch):
        result = subprocess.run(argv, cwd=path, capture_output=True, text=True, check=False)
        assert result.returncode != 0, f"push succeeded: {argv}\n{result.stderr}"
    assert refs(checkout) == before
    assert "stolen" not in refs(checkout)


def test_merge_no_ff_into_agent_default_branch(
    manager: WorktreeManager, checkout: Path, tmp_path: Path
) -> None:
    agent = tmp_path / "agent"
    manager.prepare_agent_clone(checkout, agent, "main")
    path = manager.worktree_path("acme-api", 4)
    manager.create_worktree(agent, path, "card/4-x", "main")
    (path / "x.txt").write_text("x")
    manager.safety_commit(path, "work")
    manager.remove_worktree(agent, path)
    checkout_head = git(checkout, "rev-parse", "HEAD")

    commit = manager.merge(agent, "card/4-x", "main", "Merge card 4")
    parents = git(agent, "rev-list", "--parents", "-n", "1", commit).split()
    assert len(parents) == 3  # a real merge commit, not a fast-forward
    assert (agent / "x.txt").exists()
    assert git(checkout, "rev-parse", "HEAD") == checkout_head


def test_merge_conflict_is_aborted_and_reported(
    manager: WorktreeManager, checkout: Path, tmp_path: Path
) -> None:
    agent = tmp_path / "agent"
    manager.prepare_agent_clone(checkout, agent, "main")
    path = manager.worktree_path("acme-api", 5)
    manager.create_worktree(agent, path, "card/5-x", "main")
    (path / "src" / "app.py").write_text("print('card')\n")
    manager.safety_commit(path, "card change")
    manager.remove_worktree(agent, path)
    (agent / "src" / "app.py").write_text("print('main')\n")
    git(agent, "commit", "-q", "-am", "main change")

    with pytest.raises(MergeConflict) as exc:
        manager.merge(agent, "card/5-x", "main", "Merge card 5")
    assert exc.value.files == ["src/app.py"]
    assert not Git(agent).is_dirty()
