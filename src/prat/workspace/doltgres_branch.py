"""A Doltgres branch per card for a project's database.

Doltgres is Dolt behind the Postgres wire protocol, so the card's branch works
exactly as :mod:`prat.workspace.dolt_branch` describes: ``card-<id>`` is created
from the default branch, the agent and the gates connect to the revision
database ``<database>/card-<id>``, the branch is diffed at review, merged before
the code on approve and dropped on cancel, and landed merges can be reverted or
rewound.

What differs from Dolt, as Doltgres behaves today:

- Dolt's procedures are functions: ``SELECT dolt_branch(...)``, not ``CALL``.
- ``dolt_merge`` and ``dolt_revert`` return one composite value; its fields
  (hash, fast_forward, conflicts, message) arrive as text.
- ``dolt_diff_summary`` and ``dolt_diff_stat`` take the two revisions as
  separate arguments when they are bound parameters (the one-argument
  ``'from...to'`` form only parses as a literal), so the merge base is looked up
  with ``dolt_merge_base`` first.
- Table names carry their schema (``public.orders``), and the missing side of an
  added or dropped table is an empty string rather than NULL.

PRAT connects with SQLAlchemy's ``postgresql+psycopg`` driver; the URL in
``url_env`` names the database, for example
``postgresql+psycopg://user:password@127.0.0.1:5480/app``.
"""

from __future__ import annotations

from typing import Any

from prat.workspace.dolt_branch import DoltBranchProvider, TableChange, branch_name

DEFAULT_SCHEMA = "public."


class DoltgresBranchProvider(DoltBranchProvider):
    """One Doltgres database. Every call opens and closes its own connection."""

    def _call(self, procedure: str, args: str) -> str:
        return f"SELECT {procedure.lower()}({args})"

    @staticmethod
    def _merge_conflicts(row: Any) -> int:
        if row is None or not len(row):
            return 0
        fields = row[0]
        if isinstance(fields, str):  # a driver that does not unpack the composite
            fields = fields.strip("()").split(",")
        try:
            return int(fields[2]) if len(fields) > 2 and fields[2] else 0
        except (TypeError, ValueError):
            return 0

    def changes(self, card_id: int) -> list[TableChange]:
        """What the card changed, relative to where its branch left the default branch."""
        branch = branch_name(card_id)
        base = self._scalar(
            "SELECT dolt_merge_base(:default, :branch)",
            {"default": self.spec.default_branch, "branch": branch},
        )
        span = {"base": str(base), "branch": branch}
        summary = self._rows(
            "SELECT COALESCE(NULLIF(to_table_name, ''), from_table_name), diff_type, "
            "data_change, schema_change FROM dolt_diff_summary(:base, :branch)",
            span,
        )
        stats: dict[str, tuple[int, int, int]] = {}
        for row in self._rows(
            "SELECT table_name, rows_added, rows_deleted, rows_modified "
            "FROM dolt_diff_stat(:base, :branch)",
            span,
        ):
            stats[_table(row[0])] = (int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))
        changes = []
        for table, diff_type, data_change, schema_change in summary:
            name = _table(table)
            added, deleted, modified = stats.get(name, (0, 0, 0))
            changes.append(
                TableChange(
                    name,
                    str(diff_type),
                    bool(data_change),
                    bool(schema_change),
                    added,
                    deleted,
                    modified,
                )
            )
        return sorted(changes, key=lambda change: change.table)


def _table(name: Any) -> str:
    """A table's name as people write it: tables in the default schema lose the prefix."""
    text = str(name or "")
    return text[len(DEFAULT_SCHEMA) :] if text.startswith(DEFAULT_SCHEMA) else text
