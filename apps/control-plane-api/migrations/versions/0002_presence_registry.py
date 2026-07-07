"""presence registry (T-504)

Adds the per-subject/per-source registry and per-subject tracking flag the
presence engine resolves against. `presence_history` already exists
(migration 0001) — T-504 wires a projector to it, no schema change needed
there.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "presence_sources",
        sa.Column("subject_id", sa.String(length=64), primary_key=True),
        sa.Column("source", sa.String(length=32), primary_key=True),
        sa.Column("label", sa.String(length=32), nullable=True),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "presence_subjects",
        sa.Column("subject_id", sa.String(length=64), primary_key=True),
        sa.Column("tracking_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("presence_subjects")
    op.drop_table("presence_sources")
