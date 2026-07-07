"""Mirror projections: update denormalized read models from a freshly-stored event.

Called exactly once per event, only after its raw row has been durably
inserted into `events` (see ingest.py) — never on a duplicate/replay, which
is what makes "replay the same batch N times -> identical DB state" hold:
duplicates short-circuit before any projector runs.

Every projector upserts (get-or-create then update) rather than assuming
ordering, because door-sync batches can arrive with events for the same
entity split across batches, or a later batch's finalize event landing after
an admin already saw the "started" mirror row.

`project()` dispatches with an `if/elif` chain on `event.type` (rather than
a dict of handlers) so each handler's `event` parameter can be typed as the
specific narrowed event class instead of the full `DoorboardEvent` union —
a `dict[str, Callable[[..., DoorboardEvent, ...], None]]` would force every
handler to accept the whole union and lose the type-checker's guarantee that
e.g. `event.payload.session_id` actually exists on the payload it receives.
"""

from __future__ import annotations

from datetime import datetime

from doorboard_contracts import DoorboardEvent
from doorboard_contracts.events import (
    MediaRecordingFinalizedEvent,
    MediaRecordingStartedEvent,
    MediaRetentionDeletedEvent,
    MediaThumbnailReadyEvent,
    SessionEndedEvent,
    SessionStartedEvent,
    SessionStateChangedEvent,
    SocialCheckinCreatedEvent,
    SocialGuestbookEntryCreatedEvent,
    StatusPresenceChangedEvent,
    SyncUploadCompletedEvent,
    SyncUploadFailedEvent,
    SyncUploadQueuedEvent,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane_api.models import (
    MediaMirrorRow,
    PresenceHistoryRow,
    SessionMirrorRow,
    SocialItemRow,
)


def _get_or_create_session_row(session: Session, session_id: str, door_id: str) -> SessionMirrorRow:
    row = session.get(SessionMirrorRow, session_id)
    if row is None:
        row = SessionMirrorRow(session_id=session_id, door_id=door_id)
        session.add(row)
    return row


def _get_or_create_media_row(session: Session, recording_id: str) -> MediaMirrorRow:
    row = session.get(MediaMirrorRow, recording_id)
    if row is None:
        row = MediaMirrorRow(recording_id=recording_id)
        session.add(row)
    return row


def project(session: Session, event: DoorboardEvent, *, now: datetime) -> None:
    if event.type == "session.started":
        _project_session_started(session, event, now)
    elif event.type == "session.state_changed":
        _project_session_state_changed(session, event, now)
    elif event.type == "session.ended":
        _project_session_ended(session, event, now)
    elif event.type == "media.recording_started":
        _project_media_recording_started(session, event, now)
    elif event.type == "media.recording_finalized":
        _project_media_recording_finalized(session, event, now)
    elif event.type == "media.thumbnail_ready":
        _project_media_thumbnail_ready(session, event, now)
    elif event.type == "media.retention_deleted":
        _project_media_retention_deleted(session, event, now)
    elif event.type == "sync.upload_queued":
        _project_sync_upload_queued(session, event, now)
    elif event.type == "sync.upload_completed":
        _project_sync_upload_completed(session, event, now)
    elif event.type == "sync.upload_failed":
        _project_sync_upload_failed(session, event, now)
    elif event.type == "social.guestbook_entry_created":
        _project_social_guestbook_entry_created(session, event, now)
    elif event.type == "social.checkin_created":
        _project_social_checkin_created(session, event, now)
    elif event.type == "status.presence_changed":
        _project_status_presence_changed(session, event, now)


def _project_session_started(session: Session, event: SessionStartedEvent, now: datetime) -> None:
    row = _get_or_create_session_row(session, str(event.payload.session_id), event.door_id)
    row.entry = event.payload.entry
    row.started_at = event.occurred_at
    row.updated_at = now


def _project_session_state_changed(
    session: Session, event: SessionStateChangedEvent, now: datetime
) -> None:
    row = _get_or_create_session_row(session, str(event.payload.session_id), event.door_id)
    row.state = str(event.payload.to_state)
    row.updated_at = now


def _project_session_ended(session: Session, event: SessionEndedEvent, now: datetime) -> None:
    row = _get_or_create_session_row(session, str(event.payload.session_id), event.door_id)
    row.outcome = event.payload.outcome
    row.ended_at = event.occurred_at
    row.updated_at = now


def _project_media_recording_started(
    session: Session, event: MediaRecordingStartedEvent, now: datetime
) -> None:
    row = _get_or_create_media_row(session, str(event.payload.recording_id))
    row.session_id = str(event.payload.session_id)
    row.kind = event.payload.kind
    row.stream = event.payload.stream
    row.updated_at = now


def _project_media_recording_finalized(
    session: Session, event: MediaRecordingFinalizedEvent, now: datetime
) -> None:
    row = _get_or_create_media_row(session, str(event.payload.recording_id))
    row.path = event.payload.path
    row.duration_s = event.payload.duration_s
    row.size_bytes = event.payload.size_bytes
    row.sha256 = event.payload.sha256
    row.consent_context = event.payload.consent_context
    row.updated_at = now


def _project_media_thumbnail_ready(
    session: Session, event: MediaThumbnailReadyEvent, now: datetime
) -> None:
    row = _get_or_create_media_row(session, str(event.payload.recording_id))
    row.thumbnail_path = event.payload.path
    row.updated_at = now


def _project_media_retention_deleted(
    session: Session, event: MediaRetentionDeletedEvent, now: datetime
) -> None:
    row = _get_or_create_media_row(session, str(event.payload.recording_id))
    row.deleted_at = event.occurred_at
    row.deleted_reason = event.payload.reason
    row.updated_at = now


def _project_sync_upload_queued(
    session: Session, event: SyncUploadQueuedEvent, now: datetime
) -> None:
    row = _get_or_create_media_row(session, str(event.payload.recording_id))
    row.sync_item_id = str(event.payload.item_id)
    row.sync_status = "queued"
    row.updated_at = now


def _find_media_row_by_sync_item(session: Session, item_id: str) -> MediaMirrorRow | None:
    stmt = select(MediaMirrorRow).where(MediaMirrorRow.sync_item_id == item_id)
    return session.execute(stmt).scalars().first()


def _project_sync_upload_completed(
    session: Session, event: SyncUploadCompletedEvent, now: datetime
) -> None:
    row = _find_media_row_by_sync_item(session, str(event.payload.item_id))
    if row is not None:
        row.sync_status = "synced"
        row.updated_at = now


def _project_sync_upload_failed(
    session: Session, event: SyncUploadFailedEvent, now: datetime
) -> None:
    row = _find_media_row_by_sync_item(session, str(event.payload.item_id))
    if row is not None:
        row.sync_status = "failed"
        row.updated_at = now


def _project_social_guestbook_entry_created(
    session: Session, event: SocialGuestbookEntryCreatedEvent, now: datetime
) -> None:
    item_id = str(event.payload.entry_id)
    row = session.get(SocialItemRow, ("guestbook", item_id))
    if row is None:
        row = SocialItemRow(
            kind="guestbook",
            item_id=item_id,
            door_id=event.door_id,
            created_at=event.occurred_at,
            status="active",
        )
        session.add(row)
    row.text = event.payload.text
    row.author_label = event.payload.author_label
    row.source_event_id = str(event.event_id)
    row.updated_at = now


def _project_social_checkin_created(
    session: Session, event: SocialCheckinCreatedEvent, now: datetime
) -> None:
    item_id = str(event.payload.checkin_id)
    row = session.get(SocialItemRow, ("checkin", item_id))
    if row is None:
        row = SocialItemRow(
            kind="checkin",
            item_id=item_id,
            door_id=event.door_id,
            created_at=event.occurred_at,
            status="active",
        )
        session.add(row)
    row.person_id = event.payload.person_id
    row.label = event.payload.label
    row.source_event_id = str(event.event_id)
    row.updated_at = now


def _project_status_presence_changed(
    session: Session, event: StatusPresenceChangedEvent, now: datetime
) -> None:
    # `event_id` doubles as the PK: `ingest_one` already guaranteed this
    # event wasn't a duplicate before `project()` ever runs, so there is no
    # existing row to find-or-update — every call here is a fresh insert.
    session.add(
        PresenceHistoryRow(
            event_id=str(event.event_id),
            subject_id=event.payload.subject_id,
            label=str(event.payload.label),
            source=str(event.payload.source),
            until=event.payload.until,
            occurred_at=event.occurred_at,
            recorded_at=now,
        )
    )
