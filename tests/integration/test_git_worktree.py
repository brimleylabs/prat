from __future__ import annotations

import os
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


def test_a_clone_with_commits_of_its_own_still_takes_in_the_checkouts_new_work(
    manager: WorktreeManager, checkout: Path, tmp_path: Path
) -> None:
    agent = tmp_path / "agent"
    manager.prepare_agent_clone(checkout, agent, "main")
    # PRAT's own commit (the project file) and the person's new work, made meanwhile.
    (agent / ".prat").mkdir()
    (agent / ".prat" / "project.toml").write_text('name = "acme-api"\n')
    git(agent, "add", "-A")
    git(agent, "commit", "-q", "-m", "project file")
    (checkout / "migrations").mkdir()
    (checkout / "migrations" / "0045_reviews.sql").write_text("create table reviews ();\n")
    git(checkout, "add", "-A")
    git(checkout, "commit", "-q", "-m", "migration 0045")

    info = manager.prepare_agent_clone(checkout, agent, "main")

    assert (agent / "migrations" / "0045_reviews.sql").exists()
    assert (agent / ".prat" / "project.toml").exists()
    assert any("merged" in w for w in info.warnings)
    assert (
        git(agent, "merge-base", "--is-ancestor", git(checkout, "rev-parse", "HEAD"), "HEAD") == ""
    )


def test_a_clone_whose_own_commits_conflict_stops_rather_than_run_on_stale_code(
    manager: WorktreeManager, checkout: Path, tmp_path: Path
) -> None:
    agent = tmp_path / "agent"
    manager.prepare_agent_clone(checkout, agent, "main")
    (agent / "src" / "app.py").write_text("print('agent')\n")
    git(agent, "commit", "-q", "-am", "agent side")
    (checkout / "src" / "app.py").write_text("print('person')\n")
    git(checkout, "commit", "-q", "-am", "person side")
    head = git(agent, "rev-parse", "HEAD")

    with pytest.raises(CloneError, match=r"src/app.py"):
        manager.prepare_agent_clone(checkout, agent, "main")

    assert git(agent, "rev-parse", "HEAD") == head
    assert not Git(agent).is_dirty()


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


def test_a_half_removed_worktree_is_cleared_for_the_next_run(
    manager: WorktreeManager, checkout: Path, tmp_path: Path
) -> None:
    agent = tmp_path / "agent"
    manager.prepare_agent_clone(checkout, agent, "main")
    branch = card_branch(8, "Docs")
    path = manager.worktree_path("acme-api", 8)
    manager.create_worktree(agent, path, branch, "main")
    # A removal that stopped partway: git forgot the worktree, files stayed behind.
    Git(agent).run("worktree", "remove", "--force", str(path))
    (path / "node_modules" / "pkg").mkdir(parents=True)
    (path / "node_modules" / "pkg" / "index.js").write_text("x")
    again = manager.create_worktree(agent, path, branch, "main")
    assert again.created and Git(path).current_branch() == branch
    assert not (path / "node_modules").exists()


def test_rmtree_removes_paths_past_the_windows_limit(tmp_path: Path) -> None:
    from prat.git.worktree import rmtree

    deep = tmp_path / "tree"
    target = deep
    while len(str(target)) < 300:
        target = target / "a-long-folder-name-for-nesting"
    try:
        os.makedirs("\\\\?\\" + str(target) if os.name == "nt" else target)
    except OSError:
        pytest.skip("cannot make a path this long here")
    file = Path(("\\\\?\\" if os.name == "nt" else "") + str(target / "f.txt"))
    file.write_text("x")
    rmtree(deep)
    assert not deep.exists()


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


def test_agent_commits_carry_no_assistant_attribution(
    manager: WorktreeManager, checkout: Path, tmp_path: Path
) -> None:
    agent = tmp_path / "agent"
    manager.prepare_agent_clone(checkout, agent, "main")
    path = manager.worktree_path("acme-api", 5)
    manager.create_worktree(agent, path, "card/5-x", "main")
    (path / "x.txt").write_text("x")
    git(path, "add", "-A")
    message = (
        "Add x\n\nWhy it changed.\n\nCo-Authored-By: Someone <someone@example.com>\n"
        "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_x\n"
        "\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)\n"
    )
    git(path, "commit", "-q", "-m", message)
    body = git(path, "log", "-1", "--format=%B")
    assert body.strip() == (
        "Add x\n\nWhy it changed.\n\nCo-Authored-By: Someone <someone@example.com>"
    )


def test_a_clone_can_commit_under_its_clients_author(
    manager: WorktreeManager, checkout: Path, tmp_path: Path
) -> None:
    agent = tmp_path / "agent"
    manager.prepare_agent_clone(checkout, agent, "main", author=("acme-bot", "bot@acme.example"))
    path = manager.worktree_path("acme-api", 6)
    manager.create_worktree(agent, path, "card/6-x", "main")
    (path / "x.txt").write_text("x")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "Add x")
    assert git(path, "log", "-1", "--format=%an <%ae>|%cn <%ce>").strip() == (
        "acme-bot <bot@acme.example>|acme-bot <bot@acme.example>"
    )
    # Without one the clone goes back to the installation's author on the next run.
    manager.prepare_agent_clone(checkout, agent, "main")
    assert git(agent, "config", "user.name").strip() == "PRAT agent"


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
