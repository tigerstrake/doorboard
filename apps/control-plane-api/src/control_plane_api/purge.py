"""Person-data purge (ADR-0009 §3.4).

`DELETE /people/{person_id}/events` — called by door-visiond on unenroll
(via door-sync, since the door Pi never talks to the NUC in the button
critical path). Must be idempotent and safe to retry: door-sync queues this
call durably and retries it if the NUC was unreachable, so a second (or
tenth) call with nothing left to delete must succeed cleanly rather than
error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import CursorResult, delete, or_, update
from sqlalchemy.orm import Session

from control_plane_api.models import EventRow, PersonPurgeTombstoneRow, SocialItemRow


@dataclass(frozen=True, slots=True)
class PurgeResult:
    person_id: str
    events_deleted: int
    social_items_purged: int


def purge_person(session: Session, *, person_id: str, now: datetime) -> PurgeResult:
    events_deleted = cast(
        "CursorResult",
        session.execute(delete(EventRow).where(EventRow.person_id == person_id)),
    ).rowcount
    # Scrub every social item attributed to the person, not just check-ins, and erase the
    # content — the read model kept `text`, `author_label`, and `label` after a soft-delete, so
    # a guestbook note or the check-in name survived unenrollment. Rows that still hold any of
    # that PII are marked deleted and their PII nulled; a row already scrubbed matches nothing,
    # so a retried purge (ADR-0009 §3.4 requires idempotency) touches zero rows. This also reaches
    # rows a moderator had soft-deleted, which retained their text until now.
    social_items_purged = cast(
        "CursorResult",
        session.execute(
            update(SocialItemRow)
            .where(
                SocialItemRow.person_id == person_id,
                or_(
                    SocialItemRow.text.isnot(None),
                    SocialItemRow.author_label.isnot(None),
                    SocialItemRow.label.isnot(None),
                ),
            )
            .values(
                status="deleted",
                text=None,
                author_label=None,
                label=None,
                deleted_at=now,
                deleted_reason="purge",
                updated_at=now,
            )
        ),
    ).rowcount

    tombstone = session.get(PersonPurgeTombstoneRow, person_id)
    if tombstone is None:
        session.add(
            PersonPurgeTombstoneRow(
                person_id=person_id,
                requested_at=now,
                purged_at=now,
                events_deleted_total=events_deleted,
            )
        )
    else:
        tombstone.purged_at = now
        tombstone.events_deleted_total += events_deleted
    session.flush()
    return PurgeResult(
        person_id=person_id,
        events_deleted=events_deleted,
        social_items_purged=social_items_purged,
    )
