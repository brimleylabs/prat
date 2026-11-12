"""Versioned state a card works on besides its git worktree.

Today: a Dolt or Doltgres branch per card for each database a project declares
(``[[workspace.database]]``), created next to the git branch, diffed at review,
merged together with the code on approve and dropped on cancel.
"""

from prat.workspace.dolt_branch import (
    DatabaseMergeConflict,
    DoltBranchProvider,
    TableChange,
    WorkspaceError,
)
from prat.workspace.doltgres_branch import DoltgresBranchProvider

__all__ = [
    "DatabaseMergeConflict",
    "DoltBranchProvider",
    "DoltgresBranchProvider",
    "TableChange",
    "WorkspaceError",
]
