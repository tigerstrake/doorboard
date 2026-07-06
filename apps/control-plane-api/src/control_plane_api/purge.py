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

from sqlalchemy import CursorResult, delete, update
from sqlalchemy.orm import Session

from control_plane_api.models import EventRow, PersonPurgeTombstoneRow, SocialItemRow


@dataclass(frozen=True, slots=True)
class PurgeResult:
    person_id: str
    events_deleted: int
    checkins_deleted: int


def purge_person(session: Session, *, person_id: str, now: datetime) -> PurgeResult:
    events_deleted = cast(
        "CursorResult",
        session.execute(delete(EventRow).where(EventRow.person_id == person_id)),
    ).rowcount
    checkins_deleted = cast(
        "CursorResult",
        session.execute(
            update(SocialItemRow)
            .where(
                SocialItemRow.kind == "checkin",
                SocialItemRow.person_id == person_id,
                SocialItemRow.status != "deleted",
            )
            .values(status="deleted", deleted_at=now, deleted_reason="purge", updated_at=now)
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
        person_id=person_id, events_deleted=events_deleted, checkins_deleted=checkins_deleted
    )
