"""Propagate `social.deletion_requested` against control-plane-api's durable copies.

Per events.md, `target_kind` is one of guestbook/video_message/photo/
checkin/enrollment. Each maps to a different durable store here:

- guestbook, checkin -> `social_items` (this service's own mirror)
- video_message, photo -> `media_mirror` (tombstone only; the actual file
  deletion is door-media/door-sync's job on the Pi — this just records that
  the NUC's copy of the metadata is deleted too)
- enrollment -> delegates to the person-data purge (ADR-0009 §3.4); target_id
  is the person_id in this case

All branches are no-ops (not errors) when the target row doesn't exist yet —
deletion requests can race ingestion of the thing they target, and repeated
delivery must stay idempotent.
"""

from __future__ import annotations

from datetime import datetime

from doorboard_contracts.events import SocialDeletionRequestedPayload
from sqlalchemy.orm import Session

from control_plane_api.models import MediaMirrorRow, SocialItemRow
from control_plane_api.purge import purge_person

_SOCIAL_ITEM_KINDS = {"guestbook", "checkin"}


def handle_deletion_requested(
    session: Session, payload: SocialDeletionRequestedPayload, *, now: datetime
) -> None:
    if payload.target_kind in _SOCIAL_ITEM_KINDS:
        row = session.get(SocialItemRow, (payload.target_kind, payload.target_id))
        if row is not None and row.status != "deleted":
            row.status = "deleted"
            row.deleted_at = now
            row.deleted_reason = "deletion_requested"
            row.updated_at = now
        return

    if payload.target_kind in ("video_message", "photo"):
        row = session.get(MediaMirrorRow, payload.target_id)
        if row is not None and row.deleted_at is None:
            row.deleted_at = now
            row.deleted_reason = "deletion_requested"
            row.updated_at = now
        return

    if payload.target_kind == "enrollment":
        purge_person(session, person_id=payload.target_id, now=now)
        return
