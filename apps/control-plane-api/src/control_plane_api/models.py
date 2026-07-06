"""ORM models — the durable schema this service owns on Postgres.

Table responsibilities (see apps/control-plane-api/README.md and
T-501 brief):

- `events`: append-only raw event store, idempotent by `event_id`. Source of
  truth; everything else here is a derived, best-effort mirror rebuilt from
  it.
- `service_tokens`: Pi-scoped bearer tokens (packages/auth `doorboard_auth`
  primitives; this table is the storage side).
- `session_mirror` / `media_mirror`: denormalized latest-state views over
  session.*/media.*/sync.* events, for admin/dashboard reads without
  replaying the whole event log.
- `presence_history`: append-only broad-label presence history.
- `social_items`: durable, moderatable copies of guestbook/checkin content
  (the only two social.* creation kinds that `social.deletion_requested`
  can target).
- `person_purge_tombstone`: idempotency record for the person-data purge
  (ADR-0009 §3.4).
- `notification_state`: last-fired timestamp per notification rule, so a
  persistent condition doesn't re-page on every ingested event.
- `door_configs`: versioned config bundle storage for `/config/door/{id}`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from control_plane_api.db import Base


class EventRow(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    monotonic_ms: Mapped[int] = mapped_column(BigInteger)
    door_id: Mapped[str] = mapped_column(String(64), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    # Extracted at ingest time from payloads that carry one (vision.identity_*,
    # social.checkin_created) so the person-data purge doesn't need to scan
    # every row's JSON payload. NULL for event types with no person_id field.
    person_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ServiceTokenRow(Base):
    __tablename__ = "service_tokens"

    token_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(32))
    door_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionMirrorRow(Base):
    __tablename__ = "session_mirror"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    door_id: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MediaMirrorRow(Base):
    __tablename__ = "media_mirror"

    recording_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stream: Mapped[str | None] = mapped_column(String(128), nullable=True)
    path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consent_context: Mapped[str | None] = mapped_column(String(32), nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Bridges sync.upload_queued (which carries recording_id) to
    # sync.upload_completed/failed (which only carry item_id).
    sync_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sync_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PresenceHistoryRow(Base):
    __tablename__ = "presence_history"

    # Reuses the source event_id as PK: one status.presence_changed event
    # produces exactly one history row, and idempotent-by-event_id falls out
    # for free instead of needing a second dedupe key.
    event_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("events.event_id"), primary_key=True
    )
    subject_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SocialItemRow(Base):
    __tablename__ = "social_items"

    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    door_id: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    author_label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    person_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_event_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PersonPurgeTombstoneRow(Base):
    __tablename__ = "person_purge_tombstone"

    person_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    events_deleted_total: Mapped[int] = mapped_column(Integer, default=0)


class NotificationStateRow(Base):
    __tablename__ = "notification_state"

    rule_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    last_notified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DoorConfigRow(Base):
    __tablename__ = "door_configs"

    door_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    settings: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
