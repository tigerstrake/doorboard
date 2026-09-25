"""Propagate `social.deletion_requested` against control-plane-api's durable copies.

Per events.md, `target_kind` is one of guestbook/video_message/photo/
checkin/enrollment. Each maps to a different durable store here:

- guestbook, checkin -> `social_items` (this service's own mirror) AND the raw
  `events` row the mirror was projected from (see `scrub_social_item`)
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

from control_plane_api.models import EventRow, MediaMirrorRow, SocialItemRow
from control_plane_api.purge import purge_person

_SOCIAL_ITEM_KINDS = {"guestbook", "checkin"}

# What a scrubbed raw event's payload becomes. The row itself stays (event_id,
# type, occurred_at, trace_id — the audit skeleton) so the history still shows
# that *something* was written and later deleted; only the content goes.
SCRUBBED_PAYLOAD: dict = {"scrubbed": True}


def scrub_source_event(session: Session, event_id: str) -> bool:
    """Erase the payload of the raw `events` row a social item was projected from.

    `events` is append-only by design, which is exactly why a soft-delete on the
    mirror was not a deletion at all: the guestbook sentence or the check-in name
    lived on, verbatim, in `events.payload` on the high-trust archive. ADR-0005 §6
    says every voluntary write is deletable; that has to include the copy that
    everything else is rebuilt from. Idempotent — an already-scrubbed row matches
    nothing and returns False.
    """
    event = session.get(EventRow, event_id)
    if event is None or event.payload == SCRUBBED_PAYLOAD:
        return False
    event.payload = dict(SCRUBBED_PAYLOAD)
    return True


def scrub_social_item(session: Session, row: SocialItemRow, *, now: datetime, reason: str) -> bool:
    """Mark a guestbook/check-in mirror row deleted AND erase its content everywhere.

    Nulls `text`/`author_label`/`label` exactly as `purge.purge_person` does, then
    scrubs the source event via `source_event_id`. Idempotent: a row that is already
    deleted and empty is left alone (its original `deleted_at`/`deleted_reason` are
    preserved), so a retried deletion request changes nothing.
    """
    changed = False
    if row.status != "deleted":
        row.status = "deleted"
        row.deleted_at = now
        row.deleted_reason = reason
        changed = True
    if row.text is not None or row.author_label is not None or row.label is not None:
        row.text = None
        row.author_label = None
        row.label = None
        changed = True
    if changed:
        row.updated_at = now
    scrubbed = scrub_source_event(session, row.source_event_id)
    return changed or scrubbed


def handle_deletion_requested(
    session: Session, payload: SocialDeletionRequestedPayload, *, now: datetime
) -> None:
    if payload.target_kind in _SOCIAL_ITEM_KINDS:
        row = session.get(SocialItemRow, (payload.target_kind, payload.target_id))
        if row is not None:
            scrub_social_item(session, row, now=now, reason="deletion_requested")
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
