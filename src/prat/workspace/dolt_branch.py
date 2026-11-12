"""A Dolt branch per card for a project's database (Doltgres: see doltgres_branch.py).

The card's branch ``card-<id>`` is created from the database's default branch
when the card's run is prepared. The agent and the gates see the project's own
database settings pointed at that branch (Dolt names a branch's revision
database ``<database>/<branch>``), so the application under test reads and
writes the card's data and nothing else changes for it.

At review the branch is compared with the default branch (tables, schema
changes, rows added, modified and deleted). Approve merges it into the default
branch before the code is merged, and the merge is undone if the code merge
fails, so data and code land together or not at all.

Known limit: the agent's connection is pointed at its branch, not confined to
it. A program that switches database or branch on purpose could reach another
branch. Per-card users restricted with Dolt branch permissions are the next
step; until then a project database should be a development or staging copy.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from prat.projects import DatabaseWorkspace

_BRANCH = re.compile(r"^card-\d+$")


class WorkspaceError(RuntimeError):
    pass


class DatabaseMergeConflict(WorkspaceError):
    def __init__(self, database: str, branch: str, conflicts: int) -> None:
        self.database = database
        self.branch = branch
        self.conflicts = conflicts
        super().__init__(f"merging {branch} into {database} conflicts in {conflicts} table(s)")


@dataclass(frozen=True)
class TableChange:
    table: str
    diff_type: str  # added, dropped, modified, renamed
    data_change: bool
    schema_change: bool
    rows_added: int = 0
    rows_deleted: int = 0
    rows_modified: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "diff_type": self.diff_type,
            "data_change": self.data_change,
            "schema_change": self.schema_change,
            "rows_added": self.rows_added,
            "rows_deleted": self.rows_deleted,
            "rows_modified": self.rows_modified,
        }


def branch_name(card_id: int) -> str:
    return f"card-{card_id}"


class DoltBranchProvider:
    """One project database. Every call opens and closes its own connection."""

    def __init__(self, spec: DatabaseWorkspace, url: str) -> None:
        parsed = make_url(url)
        if not parsed.database:
            raise WorkspaceError(f"{spec.url_env} names no database")
        self.spec = spec
        self.url = url
        self.database = parsed.database.split("/", 1)[0]

    @classmethod
    def from_spec(
        cls, spec: DatabaseWorkspace, environ: Mapping[str, str] | None = None
    ) -> DoltBranchProvider:
        env = os.environ if environ is None else environ
        url = env.get(spec.url_env, "").strip()
        if not url:
            raise WorkspaceError(
                f"database {spec.name!r}: set {spec.url_env} in PRAT's environment to its URL"
            )
        return cls(spec, url)

    # Connections ---------------------------------------------------------------------

    def _engine(self, revision: str | None = None) -> Engine:
        parsed = make_url(self.url)
        target = f"{self.database}/{revision}" if revision else self.database
        return create_engine(parsed.set(database=target), isolation_level="AUTOCOMMIT")

    def _scalar(
        self, sql: str, params: dict[str, Any] | None = None, revision: str | None = None
    ) -> Any:
        engine = self._engine(revision)
        try:
            with engine.connect() as connection:
                return connection.execute(text(sql), params or {}).scalar()
        finally:
            engine.dispose()

    def _call(self, procedure: str, args: str) -> str:
        """The statement that runs one of Dolt's stored procedures. Doltgres (the Postgres
        flavour) calls the same procedures as functions in a ``SELECT``."""
        return f"CALL {procedure.upper()}({args})"

    def _rows(self, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
        engine = self._engine()
        try:
            with engine.connect() as connection:
                return list(connection.execute(text(sql), params or {}).all())
        finally:
            engine.dispose()

    # Branch lifecycle ----------------------------------------------------------------

    def exists(self, card_id: int) -> bool:
        count = self._scalar(
            "SELECT COUNT(*) FROM dolt_branches WHERE name = :name", {"name": branch_name(card_id)}
        )
        return bool(count)

    def create(self, card_id: int) -> str:
        """Create the card's branch from the default branch (once). Returns its head."""
        branch = branch_name(card_id)
        if not self.exists(card_id):
            self.settle_default()
            self._scalar(
                self._call("dolt_branch", ":branch, :base"),
                {"branch": branch, "base": self.spec.default_branch},
            )
        return self.head(card_id)

    def head(self, card_id: int) -> str:
        return str(self._scalar("SELECT HASHOF(:ref)", {"ref": branch_name(card_id)}))

    def default_head(self) -> str:
        return str(self._scalar("SELECT HASHOF(:ref)", {"ref": self.spec.default_branch}))

    def commit(self, card_id: int, message: str) -> str | None:
        """Commit what the agent wrote on its branch. Returns the new head, or None
        when nothing changed."""
        return self._commit_revision(branch_name(card_id), message)

    def settle_default(self) -> str | None:
        """Commit anything written to the default branch outside a card (a setup script's
        migrations, an environment run from the checkout), so a card branches from, and is
        compared with, the data as it really is. Returns the new head, or None."""
        return self._commit_revision(
            self.spec.default_branch, "PRAT: changes made outside a card", author="prat"
        )

    def _commit_revision(
        self, branch: str, message: str, *, author: str = "PRAT agent"
    ) -> str | None:
        engine = self._engine(branch)
        try:
            with engine.connect() as connection:
                try:
                    connection.execute(
                        text(
                            self._call("dolt_commit", "'-A', '-m', :message, '--author', :author")
                        ),
                        {
                            "message": " ".join(message.split())[:500],
                            "author": f"{author} <agent@prat.invalid>",
                        },
                    )
                except Exception as exc:
                    if "nothing to commit" in str(exc).lower():
                        return None
                    raise
                value = connection.execute(text("SELECT HASHOF('HEAD')")).scalar()
        finally:
            engine.dispose()
        return str(value)

    def discard(self, card_id: int) -> bool:
        if not self.exists(card_id):
            return False
        self._scalar(self._call("dolt_branch", "'-D', :branch"), {"branch": branch_name(card_id)})
        return True

    # What the agent sees -------------------------------------------------------------

    def branch_url(self, card_id: int, *, driver: str | None = None) -> str:
        parsed = make_url(self.url).set(database=f"{self.database}/{branch_name(card_id)}")
        if driver is not None:
            parsed = parsed.set(drivername=driver)
        return parsed.render_as_string(hide_password=False)

    def agent_env(self, card_id: int) -> dict[str, str]:
        """The project's own database settings, pointed at the card's branch."""
        env = {self.spec.url_env: self.branch_url(card_id)}
        if self.spec.database_env:
            env[self.spec.database_env] = f"{self.database}/{branch_name(card_id)}"
        if self.spec.app_url_env:
            backend = make_url(self.url).get_backend_name()
            env[self.spec.app_url_env] = self.branch_url(card_id, driver=backend)
        return env

    # Review --------------------------------------------------------------------------

    def changes(self, card_id: int) -> list[TableChange]:
        """What the card changed, relative to where its branch left the default branch."""
        span = f"{self.spec.default_branch}...{branch_name(card_id)}"
        summary = self._rows(
            "SELECT COALESCE(to_table_name, from_table_name), diff_type, data_change, "
            "schema_change FROM DOLT_DIFF_SUMMARY(:span)",
            {"span": span},
        )
        stats: dict[str, tuple[int, int, int]] = {}
        for row in self._rows(
            "SELECT table_name, rows_added, rows_deleted, rows_modified FROM DOLT_DIFF_STAT(:span)",
            {"span": span},
        ):
            stats[str(row[0])] = (int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))
        changes = []
        for table, diff_type, data_change, schema_change in summary:
            added, deleted, modified = stats.get(str(table), (0, 0, 0))
            changes.append(
                TableChange(
                    str(table),
                    str(diff_type),
                    bool(data_change),
                    bool(schema_change),
                    added,
                    deleted,
                    modified,
                )
            )
        return sorted(changes, key=lambda change: change.table)

    # Landing -------------------------------------------------------------------------

    def merge(self, card_id: int, message: str) -> tuple[str, str]:
        """Merge the card's branch into the default branch. Returns (the default
        branch's head before, the merge commit). Conflicts abort and raise."""
        branch = branch_name(card_id)
        self.settle_default()
        engine = self._engine(self.spec.default_branch)
        try:
            with engine.connect() as connection:
                before = str(connection.execute(text("SELECT HASHOF('HEAD')")).scalar())
                try:
                    row = connection.execute(
                        text(self._call("dolt_merge", "'--no-ff', '-m', :message, :branch")),
                        {"message": " ".join(message.split())[:500], "branch": branch},
                    ).first()
                except Exception as exc:
                    # With autocommit on, Dolt rolls a conflicting merge back itself.
                    if "conflict" in str(exc).lower():
                        raise DatabaseMergeConflict(self.database, branch, 1) from exc
                    raise
                conflicts = self._merge_conflicts(row)
                if conflicts:
                    connection.execute(text(self._call("dolt_merge", "'--abort'")))
                    raise DatabaseMergeConflict(self.database, branch, conflicts)
                after = str(connection.execute(text("SELECT HASHOF('HEAD')")).scalar())
        finally:
            engine.dispose()
        return before, after

    @staticmethod
    def _merge_conflicts(row: Any) -> int:
        """The conflict count in ``DOLT_MERGE``'s result row (hash, fast_forward, conflicts,
        message)."""
        return int(row[2]) if row is not None and len(row) > 2 and row[2] else 0

    def undo_merge(self, before: str) -> None:
        """Put the default branch back where it was before a merge."""
        self.reset_to(before)

    def reset_to(self, commit: str) -> None:
        """Move the default branch to ``commit``, dropping what came after (rewind)."""
        if not re.fullmatch(r"[0-9a-v]{32}", commit):
            raise WorkspaceError(f"not a Dolt commit: {commit!r}")
        self._scalar(
            self._call("dolt_reset", "'--hard', :commit"),
            {"commit": commit},
            self.spec.default_branch,
        )

    def revert(self, commit: str) -> str:
        """Reverse one commit (a card's merge) on the default branch and keep the rest.
        Returns the new head. A conflict changes nothing and raises."""
        if not re.fullmatch(r"[0-9a-v]{32}", commit):
            raise WorkspaceError(f"not a Dolt commit: {commit!r}")
        engine = self._engine(self.spec.default_branch)
        try:
            with engine.connect() as connection:
                try:
                    connection.execute(
                        text(self._call("dolt_revert", ":commit")), {"commit": commit}
                    )
                except Exception as exc:
                    if "conflict" in str(exc).lower():
                        raise DatabaseMergeConflict(self.database, commit[:10], 1) from exc
                    raise
                return str(connection.execute(text("SELECT HASHOF('HEAD')")).scalar())
        finally:
            engine.dispose()

    def tag(self, name: str, commit: str, message: str = "") -> None:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")[:120]
        self._scalar(
            self._call("dolt_tag", ":name, :commit, '-m', :message"),
            {"name": safe, "commit": commit, "message": message or safe},
        )


def providers_for(
    databases: list[DatabaseWorkspace], environ: Mapping[str, str] | None = None
) -> list[DoltBranchProvider]:
    """A provider per declared database, by its ``kind``: Dolt, Doltgres, or plain PostgreSQL
    (a copy per card). A Doltgres database whose address turns out to be plain PostgreSQL (the
    project fell back) gets the copy provider."""
    from prat.workspace.doltgres_branch import DoltgresBranchProvider
    from prat.workspace.postgres_copy import PostgresCopyProvider, is_doltgres

    kinds: dict[str, type[DoltBranchProvider]] = {
        "dolt": DoltBranchProvider,
        "doltgres": DoltgresBranchProvider,
        "postgres": PostgresCopyProvider,
    }
    found = []
    for spec in databases:
        provider = kinds[spec.kind].from_spec(spec, environ)
        if spec.kind == "doltgres" and not is_doltgres(provider.url):
            provider = PostgresCopyProvider(spec, provider.url)
        found.append(provider)
    return found


def is_card_branch(name: str) -> bool:
    return bool(_BRANCH.match(name))
