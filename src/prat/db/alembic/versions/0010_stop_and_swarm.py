"""runs: a stop that ends one run and keeps the card; cards: the branch a swarm part builds on

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMNs: SQLite adds them in place, so neither table (nor the rows that cascade
    # from it) is copied and recreated.
    op.add_column("runs", sa.Column("stop_only", sa.Boolean(), nullable=True))
    op.add_column("cards", sa.Column("base_branch", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cards", schema=None) as batch_op:
        batch_op.drop_column("base_branch")
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_column("stop_only")
