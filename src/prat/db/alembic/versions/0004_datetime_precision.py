"""microsecond datetimes on MySQL and Dolt

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-11

MySQL and Dolt store DATETIME to whole seconds by default, so two events in the
same second could not be ordered. Every datetime column becomes DATETIME(6).
SQLite keeps full precision already; nothing changes there.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _datetime_columns() -> list[tuple[str, str, bool]]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    found: list[tuple[str, str, bool]] = []
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        for column in inspector.get_columns(table):
            if isinstance(column["type"], sa.DateTime):
                found.append((table, column["name"], bool(column["nullable"])))
    return found


def _retype(fsp: int | None) -> None:
    if op.get_bind().dialect.name not in ("mysql", "mariadb"):
        return
    for table, column, nullable in _datetime_columns():
        op.alter_column(
            table,
            column,
            type_=mysql.DATETIME(fsp=fsp) if fsp else mysql.DATETIME(),
            existing_nullable=nullable,
        )


def upgrade() -> None:
    _retype(6)


def downgrade() -> None:
    _retype(None)
