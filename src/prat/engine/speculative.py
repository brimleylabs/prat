"""Speculative review: the reviewer starts when the builder runs the tests.

A builder ends by running the project's tests and saying what it did, a stretch of seconds in
which nothing changes. When a builder runs a test command after editing, PRAT takes a snapshot of
its worktree (a commit object made with a separate index, so the builder's own index, branch and
files are untouched) and starts the reviewer on it. The verdict is kept only if the work that
reaches the gates is that same tree; any change after the snapshot discards it and the reviewer
runs again. A review is always of exactly the code that is handed over.
"""

from __future__ import annotations

import shlex
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path

from prat.git import Git
from prat.projects import ProjectSpec, command_argv

EDIT_TOOLS = frozenset({"write", "edit", "multiedit", "notebookedit"})
TEST_WORDS = (
    "pytest",
    "unittest",
    "npm test",
    "pnpm test",
    "yarn test",
    "go test",
    "cargo test",
    "jest",
    "vitest",
    "phpunit",
    "artisan test",
    "rspec",
)


def test_markers(spec: ProjectSpec) -> list[str]:
    """Pieces of the project's gate commands that show a shell command runs them."""
    markers: list[str] = list(TEST_WORDS)
    for name in spec.verify.gates:
        command = spec.commands.get(name)
        if command is None:
            continue
        argv = [a for a in command_argv(command) if not a.startswith("{")]
        words = [a for a in argv[1:] if not a.startswith("-")][:2]
        if words:
            markers.append(" ".join(words))
        elif argv:
            markers.append(Path(argv[0]).name)
    return markers


def runs_tests(command: str, markers: Iterable[str]) -> bool:
    try:
        text = " ".join(shlex.split(command, posix=True))
    except ValueError:  # unbalanced quotes: look at the text as it is
        text = command
    return any(marker and marker in text for marker in markers)


def snapshot(worktree: Path) -> tuple[str, str] | None:
    """``(tree, commit)`` for the worktree as it is now, or ``None`` if git could not say."""
    with tempfile.TemporaryDirectory() as tmp:
        # PRAT's own git settings (line endings and the rest), as its commits are made with.
        env = {**Git(worktree)._env(), "GIT_INDEX_FILE": str(Path(tmp) / "index")}

        def git(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", *args], cwd=worktree, env=env, capture_output=True, text=True, timeout=120
            )

        if git("read-tree", "HEAD").returncode or git("add", "--all").returncode:
            return None
        tree = git("write-tree")
        if tree.returncode:
            return None
        tree_id = tree.stdout.strip()
        commit = git(
            "-c",
            "user.name=PRAT",
            "-c",
            "user.email=prat@prat.invalid",
            "commit-tree",
            tree_id,
            "-p",
            "HEAD",
            "-m",
            "PRAT: a snapshot for an early review",
        )
        if commit.returncode:
            return None
        return tree_id, commit.stdout.strip()


def tree_of(repo: Path, ref: str = "HEAD") -> str | None:
    out = Git(repo).run("rev-parse", f"{ref}^{{tree}}", check=False)
    return out.stdout.strip() if out.ok else None
