"""A copy of a plain PostgreSQL database per card: the fallback for projects that cannot run
on Doltgres.

Some applications use SQL that Doltgres does not support yet. PRAT then keeps the project on
plain PostgreSQL (``project`` or ``fallback_database_url``) and still gives every card a
database of its own: ``<database>__card_<id>``, a copy of the project database made when the
card's run is prepared (``CREATE DATABASE ... TEMPLATE``, or ``pg_dump`` into a new database
when something is connected to the project database, which a template copy does not allow).

What Doltgres branches give and a copy cannot: there is no diff of the card's data at review,
no merge of its data on approve, and no undo of data. A card's schema changes reach the project
database through the project's own migrations (its setup command), as they would in any other
environment; the card's copy is dropped when the card is cancelled.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from prat.workspace.dolt_branch import DoltBranchProvider, TableChange, WorkspaceError

COPY_TIMEOUT_SECONDS = 900
# Recorded as the "head" of a copy: there is no history to bind approval to.
NO_HISTORY = "postgres-copy"


def copy_name(database: str, card_id: int) -> str:
    return f"{database}__card_{card_id}"[:63]


class PostgresCopyProvider(DoltBranchProvider):
    """One plain PostgreSQL project database; a full copy of it per card."""

    def _engine(self, revision: str | None = None) -> Engine:
        # ``revision`` is a database name here (a card's copy), or None for the project's.
        target = revision or self.database
        url = make_url(self.url).set(database=target)
        return create_engine(url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)

    def _admin(self) -> Engine:
        return self._engine("postgres")

    def _admin_scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        engine = self._admin()
        try:
            with engine.connect() as connection:
                return connection.execute(text(sql), params or {}).scalar()
        finally:
            engine.dispose()

    # Lifecycle -------------------------------------------------------------------------

    def exists(self, card_id: int) -> bool:
        found = self._admin_scalar(
            "SELECT 1 FROM pg_database WHERE datname = :name",
            {"name": copy_name(self.database, card_id)},
        )
        return found is not None

    def create(self, card_id: int) -> str:
        if self.exists(card_id):
            return NO_HISTORY
        name = copy_name(self.database, card_id)
        try:
            self._admin_scalar(f'CREATE DATABASE "{name}" TEMPLATE "{self.database}"')
        except Exception as exc:
            if "being accessed by other users" not in str(exc):
                raise WorkspaceError(f"could not copy {self.database}: {exc}") from exc
            self._copy_with_dump(name)
        return NO_HISTORY

    def _copy_with_dump(self, name: str) -> None:
        """Copy a database that something is connected to: pg_dump into a new database."""
        dump, psql = shutil.which("pg_dump"), shutil.which("psql")
        if not dump or not psql:
            raise WorkspaceError(
                f"{self.database} is in use, so it cannot be copied as a template, and "
                "pg_dump and psql are not on the PATH to copy it another way"
            )
        self._admin_scalar(f'CREATE DATABASE "{name}"')
        source = make_url(self.url).set(drivername="postgresql")
        target = source.set(database=name)
        with subprocess.Popen(
            [dump, "--no-owner", "--no-privileges", source.render_as_string(hide_password=False)],
            stdout=subprocess.PIPE,
        ) as producer:
            loaded = subprocess.run(
                [psql, "-q", "-v", "ON_ERROR_STOP=1", target.render_as_string(hide_password=False)],
                stdin=producer.stdout,
                capture_output=True,
                timeout=COPY_TIMEOUT_SECONDS,
                check=False,
            )
        if producer.returncode or loaded.returncode:
            self.discard_name(name)
            raise WorkspaceError(f"copying {self.database} failed: {loaded.stderr.decode()[-400:]}")

    def head(self, card_id: int) -> str:
        return NO_HISTORY

    def default_head(self) -> str:
        return NO_HISTORY

    def commit(self, card_id: int, message: str) -> str | None:
        return None

    def settle_default(self) -> str | None:
        return None

    def discard(self, card_id: int) -> bool:
        if not self.exists(card_id):
            return False
        self.discard_name(copy_name(self.database, card_id))
        return True

    def discard_name(self, name: str) -> None:
        self._admin_scalar(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    # What the agent sees ---------------------------------------------------------------

    def branch_url(self, card_id: int, *, driver: str | None = None) -> str:
        parsed = make_url(self.url).set(database=copy_name(self.database, card_id))
        if driver is not None:
            parsed = parsed.set(drivername=driver)
        return parsed.render_as_string(hide_password=False)

    def agent_env(self, card_id: int) -> dict[str, str]:
        env = {self.spec.url_env: self.branch_url(card_id)}
        if self.spec.database_env:
            env[self.spec.database_env] = copy_name(self.database, card_id)
        if self.spec.app_url_env:
            backend = make_url(self.url).get_backend_name()
            env[self.spec.app_url_env] = self.branch_url(card_id, driver=backend)
        return env

    # Review and landing: a copy has no history --------------------------------------------

    def changes(self, card_id: int) -> list[TableChange]:
        return []

    def merge(self, card_id: int, message: str) -> tuple[str, str]:
        # The card's data stays in its copy; its schema reaches the project database through
        # the project's migrations.
        return NO_HISTORY, NO_HISTORY

    def undo_merge(self, before: str) -> None:
        return None

    def reset_to(self, commit: str) -> None:
        raise WorkspaceError(
            f"{self.database} is plain PostgreSQL (the Doltgres fallback): its data has no "
            "history to rewind; only the code is put back"
        )

    def revert(self, commit: str) -> str:
        return NO_HISTORY

    def tag(self, name: str, commit: str, message: str = "") -> None:
        return None


_DOLTGRES: dict[str, bool] = {}


def is_doltgres(url: str) -> bool:
    """Whether the server behind ``url`` is Doltgres (it has dolt_version()); remembered per
    server. A server that cannot be reached counts as Doltgres, so the error shows up where
    the database is used."""
    parsed = make_url(url)
    key = f"{parsed.host}:{parsed.port}"
    if key not in _DOLTGRES:
        engine = None
        try:
            engine = create_engine(parsed, isolation_level="AUTOCOMMIT")
            with engine.connect() as connection:
                connection.execute(text("SELECT dolt_version()"))
            _DOLTGRES[key] = True
        except Exception as exc:
            _DOLTGRES[key] = "dolt_version" not in str(exc).lower()
        finally:
            if engine is not None:
                engine.dispose()
    return _DOLTGRES[key]
