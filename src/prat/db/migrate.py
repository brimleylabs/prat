"""Run Alembic migrations programmatically.

The migration scripts ship inside the package (``PRAT/db/alembic``) so that
``prat db upgrade`` works from an installed wheel as well as a checkout.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from prat.db.database import Database

ALEMBIC_DIR = Path(__file__).parent / "alembic"


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", ALEMBIC_DIR.as_posix())
    # Percent signs in URLs must be escaped for ConfigParser interpolation.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


class MigrationError(RuntimeError):
    pass


def upgrade(db: Database, revision: str = "head") -> None:
    cfg = alembic_config(db.url)
    if db.engine.dialect.name != "sqlite":
        with db.engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, revision)
        return
    # SQLite changes a table's constraints by building a new table and dropping the old one.
    # With foreign keys enforced, that drop deletes the old table's rows first, and every
    # ON DELETE CASCADE that points at it empties the tables that refer to it (a project's
    # cards, runs and comments). So enforcement is off while migrating (the pragma only takes
    # effect outside a transaction), and the result is checked before it is committed.
    with db.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin():
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, revision)
                broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise MigrationError(f"migration left rows without their parents: {broken[:5]}")
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def current_revision(db: Database) -> str | None:
    with db.engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str | None:
    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    return script.get_current_head()


def is_current(db: Database) -> bool:
    return current_revision(db) == head_revision()
