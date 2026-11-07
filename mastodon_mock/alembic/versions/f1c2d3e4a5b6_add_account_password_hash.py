"""add accounts.password_hash

Revision ID: f1c2d3e4a5b6
Revises: e7a91c5b3d42
Create Date: 2026-11-07 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1c2d3e4a5b6"
down_revision: str | None = "e7a91c5b3d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("password_hash", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_column("password_hash")
