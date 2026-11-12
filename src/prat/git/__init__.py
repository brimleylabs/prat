"""Git operations: a safe runner plus agent clone and worktree management."""

from prat.git.runner import Git, GitError, GitResult, check_branch_name
from prat.git.worktree import (
    PUSH_SENTINEL,
    CloneError,
    CloneInfo,
    DiffFile,
    DiffStats,
    MergeConflict,
    MergeError,
    WorktreeInfo,
    WorktreeManager,
    card_branch,
)

__all__ = [
    "PUSH_SENTINEL",
    "CloneError",
    "CloneInfo",
    "DiffFile",
    "DiffStats",
    "Git",
    "GitError",
    "GitResult",
    "MergeConflict",
    "MergeError",
    "WorktreeInfo",
    "WorktreeManager",
    "card_branch",
    "check_branch_name",
]
