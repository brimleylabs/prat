"""clients: an optional commit author for agents' work on the client's projects

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clients", schema=None) as batch_op:
        batch_op.add_column(sa.Column("git_author_name", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("git_author_email", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("clients", schema=None) as batch_op:
        batch_op.drop_column("git_author_email")
        batch_op.drop_column("git_author_name")
