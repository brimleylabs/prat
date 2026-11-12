"""Choosing and opening PRAT's store.

Three kinds:

``external``  ``database_url`` is set: PRAT connects to it (SQLite, Postgres,
              or a Dolt server someone else runs).
``dolt``      PRAT's own managed ``dolt sql-server`` under ``$PRAT_HOME/store``.
``sqlite``    ``$PRAT_HOME/prat.db``, the zero-install fallback.

With ``store = "auto"`` (the default) an installation never changes kind by
itself: a home that already has a SQLite database keeps it, a home that
already has a Dolt store keeps that, and only a fresh home picks Dolt when a
Dolt binary is found. Moving an existing installation is an explicit command.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from prat.config import Settings
from prat.db import Database
from prat.dolt.server import DATABASE, DoltError, ManagedDolt, find_dolt


class StoreKind(StrEnum):
    EXTERNAL = "external"
    DOLT = "dolt"
    SQLITE = "sqlite"


def store_dir(settings: Settings) -> Path:
    return settings.home / "store"


def sqlite_file(settings: Settings) -> Path:
    return settings.sqlite_path


def has_dolt_store(settings: Settings) -> bool:
    return (store_dir(settings) / DATABASE / ".dolt").is_dir()


def choose(settings: Settings) -> StoreKind:
    if settings.database_url:
        return StoreKind.EXTERNAL
    if settings.store == "sqlite":
        return StoreKind.SQLITE
    if settings.store == "dolt":
        return StoreKind.DOLT
    if sqlite_file(settings).exists():
        return StoreKind.SQLITE
    if has_dolt_store(settings):
        return StoreKind.DOLT
    return StoreKind.DOLT if find_dolt(settings.dolt_path) else StoreKind.SQLITE


@dataclass
class Store:
    kind: StoreKind
    db: Database
    server: ManagedDolt | None = None

    @property
    def is_dolt(self) -> bool:
        if self.kind is StoreKind.DOLT:
            return True
        return self.db.engine.dialect.name == "mysql" and _answers_dolt(self.db)

    def close(self) -> None:
        self.db.dispose()
        if self.server is not None:
            self.server.stop()


def _answers_dolt(db: Database) -> bool:
    try:
        with db.engine.connect() as connection:
            connection.exec_driver_sql("SELECT dolt_version()").scalar()
    except Exception:
        return False
    return True


def open_store(settings: Settings) -> Store:
    """Open the store this installation uses, starting PRAT's Dolt server if needed.

    The caller closes it; a server this process started stops then.
    """
    kind = choose(settings)
    if kind is StoreKind.EXTERNAL and settings.database_url:
        return Store(kind, Database(settings.database_url))
    if kind is StoreKind.SQLITE:
        return Store(kind, Database(f"sqlite:///{sqlite_file(settings).as_posix()}"))
    binary = find_dolt(settings.dolt_path)
    if binary is None:
        where = settings.dolt_path or "the PATH"
        raise DoltError(
            f"this installation uses the Dolt store but no dolt binary was found on {where}; "
            "install Dolt or set dolt_path"
        )
    server = ManagedDolt(binary, store_dir(settings))
    server.start()
    try:
        return Store(kind, Database(server.url()), server)
    except Exception:
        server.stop()
        raise
