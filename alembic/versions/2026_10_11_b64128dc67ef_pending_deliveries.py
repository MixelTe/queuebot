"""Persist message deliveries separately from webhook command execution.

Revision ID: b64128dc67ef
Revises: 17a58065a1ed
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b64128dc67ef"
down_revision: Union[str, Sequence[str], None] = "17a58065a1ed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ProcessedUpdate") as batch_op:
        batch_op.add_column(sa.Column("deliveries", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ProcessedUpdate") as batch_op:
        batch_op.drop_column("deliveries")
