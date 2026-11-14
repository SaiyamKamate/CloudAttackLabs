"""add scenario_runs.summary

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13 16:30:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scenario_runs", sa.Column("summary", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("scenario_runs", "summary")
