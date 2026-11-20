"""registered targets and their authorizations

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-20 10:00:00.000000

Adds `targets` (an application registered by URL, run outside CloudAttackLab)
and `target_authorizations`. No existing table changes: each target's runs,
events, alerts and incidents belong to an ordinary `labs` row with
environment "external".
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=300), nullable=False),
        sa.Column("resolved_ip", sa.String(length=45), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("integration_key", sa.Text(), nullable=False),
        sa.Column("control_token", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("instance_id", sa.String(length=64), nullable=True),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telemetry_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lab_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lab_id"], ["labs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("lab_id"),
    )
    op.create_table(
        "target_authorizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("authorized_by", sa.String(length=64), nullable=False),
        sa.Column("acknowledgement", sa.Text(), nullable=False),
        sa.Column("max_runs", sa.Integer(), nullable=False),
        sa.Column("runs_used", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_target_authorizations_lookup", "target_authorizations", ["target_id", "status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_target_authorizations_lookup", table_name="target_authorizations")
    op.drop_table("target_authorizations")
    op.drop_table("targets")
    # The external labs stay as ordinary labs (their runs and evidence are kept);
    # nothing can drive them without a target, so mark them stopped.
    op.execute("UPDATE labs SET status = 'stopped' WHERE environment = 'external'")
