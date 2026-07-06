"""Unit-level coverage of deletion.handle_deletion_requested for each target_kind."""

from __future__ import annotations

from datetime import UTC, datetime

from control_plane_api.deletion import handle_deletion_requested
from control_plane_api.models import MediaMirrorRow, PersonPurgeTombstoneRow
from doorboard_contracts.events import SocialDeletionRequestedPayload

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)


def test_video_message_deletion_tombstones_media_mirror_row(session_factory) -> None:
    with session_factory() as session:
        session.add(MediaMirrorRow(recording_id="rec-1", kind="video_message", updated_at=NOW))
        session.commit()

        handle_deletion_requested(
            session,
            SocialDeletionRequestedPayload(target_kind="video_message", target_id="rec-1"),
            now=NOW,
        )
        session.commit()

        row = session.get(MediaMirrorRow, "rec-1")
        assert row.deleted_at == NOW
        assert row.deleted_reason == "deletion_requested"


def test_photo_deletion_tombstones_media_mirror_row(session_factory) -> None:
    with session_factory() as session:
        session.add(MediaMirrorRow(recording_id="photo-1", kind="photo_booth", updated_at=NOW))
        session.commit()

        handle_deletion_requested(
            session,
            SocialDeletionRequestedPayload(target_kind="photo", target_id="photo-1"),
            now=NOW,
        )
        session.commit()

        row = session.get(MediaMirrorRow, "photo-1")
        assert row.deleted_at == NOW


def test_enrollment_deletion_delegates_to_person_purge(session_factory) -> None:
    with session_factory() as session:
        handle_deletion_requested(
            session,
            SocialDeletionRequestedPayload(target_kind="enrollment", target_id="prs_abc"),
            now=NOW,
        )
        session.commit()

        tombstone = session.get(PersonPurgeTombstoneRow, "prs_abc")
        assert tombstone is not None
        assert tombstone.purged_at == NOW


def test_deletion_of_already_deleted_media_is_a_no_op(session_factory) -> None:
    with session_factory() as session:
        earlier = datetime(2026, 7, 1, tzinfo=UTC)
        session.add(
            MediaMirrorRow(
                recording_id="rec-2",
                kind="video_message",
                deleted_at=earlier,
                deleted_reason="age",
                updated_at=earlier,
            )
        )
        session.commit()

        handle_deletion_requested(
            session,
            SocialDeletionRequestedPayload(target_kind="video_message", target_id="rec-2"),
            now=NOW,
        )
        session.commit()

        row = session.get(MediaMirrorRow, "rec-2")
        assert row.deleted_at == earlier  # unchanged — already deleted
        assert row.deleted_reason == "age"
