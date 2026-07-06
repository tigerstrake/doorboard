"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-06

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=64), primary_key=True),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("monotonic_ms", sa.BigInteger(), nullable=False),
        sa.Column("door_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("person_id", sa.String(length=64), nullable=True),
        sa.Column("batch_id", sa.String(length=64), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_events_type", "events", ["type"])
    op.create_index("ix_events_occurred_at", "events", ["occurred_at"])
    op.create_index("ix_events_door_id", "events", ["door_id"])
    op.create_index("ix_events_trace_id", "events", ["trace_id"])
    op.create_index("ix_events_person_id", "events", ["person_id"])

    op.create_table(
        "service_tokens",
        sa.Column("token_id", sa.String(length=32), primary_key=True),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("door_id", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_service_tokens_door_id", "service_tokens", ["door_id"])

    op.create_table(
        "session_mirror",
        sa.Column("session_id", sa.String(length=64), primary_key=True),
        sa.Column("door_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=True),
        sa.Column("entry", sa.String(length=32), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_session_mirror_door_id", "session_mirror", ["door_id"])

    op.create_table(
        "media_mirror",
        sa.Column("recording_id", sa.String(length=64), primary_key=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=True),
        sa.Column("stream", sa.String(length=128), nullable=True),
        sa.Column("path", sa.String(length=512), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("consent_context", sa.String(length=32), nullable=True),
        sa.Column("thumbnail_path", sa.String(length=512), nullable=True),
        sa.Column("sync_item_id", sa.String(length=64), nullable=True),
        sa.Column("sync_status", sa.String(length=32), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_reason", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_media_mirror_session_id", "media_mirror", ["session_id"])
    op.create_index("ix_media_mirror_sync_item_id", "media_mirror", ["sync_item_id"])

    op.create_table(
        "presence_history",
        sa.Column(
            "event_id",
            sa.String(length=64),
            sa.ForeignKey("events.event_id"),
            primary_key=True,
        ),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_presence_history_subject_id", "presence_history", ["subject_id"])
    op.create_index("ix_presence_history_occurred_at", "presence_history", ["occurred_at"])

    op.create_table(
        "social_items",
        sa.Column("kind", sa.String(length=32), primary_key=True),
        sa.Column("item_id", sa.String(length=64), primary_key=True),
        sa.Column("door_id", sa.String(length=64), nullable=False),
        sa.Column("text", sa.String(length=2048), nullable=True),
        sa.Column("author_label", sa.String(length=256), nullable=True),
        sa.Column("person_id", sa.String(length=64), nullable=True),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("source_event_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_reason", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_social_items_door_id", "social_items", ["door_id"])
    op.create_index("ix_social_items_person_id", "social_items", ["person_id"])

    op.create_table(
        "person_purge_tombstone",
        sa.Column("person_id", sa.String(length=64), primary_key=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("events_deleted_total", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "notification_state",
        sa.Column("rule_key", sa.String(length=128), primary_key=True),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "door_configs",
        sa.Column("door_id", sa.String(length=64), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("door_configs")
    op.drop_table("notification_state")
    op.drop_table("person_purge_tombstone")
    op.drop_index("ix_social_items_person_id", table_name="social_items")
    op.drop_index("ix_social_items_door_id", table_name="social_items")
    op.drop_table("social_items")
    op.drop_index("ix_presence_history_occurred_at", table_name="presence_history")
    op.drop_index("ix_presence_history_subject_id", table_name="presence_history")
    op.drop_table("presence_history")
    op.drop_index("ix_media_mirror_sync_item_id", table_name="media_mirror")
    op.drop_index("ix_media_mirror_session_id", table_name="media_mirror")
    op.drop_table("media_mirror")
    op.drop_index("ix_session_mirror_door_id", table_name="session_mirror")
    op.drop_table("session_mirror")
    op.drop_index("ix_service_tokens_door_id", table_name="service_tokens")
    op.drop_table("service_tokens")
    op.drop_index("ix_events_person_id", table_name="events")
    op.drop_index("ix_events_trace_id", table_name="events")
    op.drop_index("ix_events_door_id", table_name="events")
    op.drop_index("ix_events_occurred_at", table_name="events")
    op.drop_index("ix_events_type", table_name="events")
    op.drop_table("events")
