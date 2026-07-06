"""Core ingest logic: validate, idempotently store, and project one event.

Kept separate from the HTTP layer (routes.py) so it's testable without a
FastAPI app, and separate from notify/mqtt fan-out (service.py) so
duplicate-detection and mirror projection have no dependency on the
notifier/broker being reachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from doorboard_contracts import DoorboardEvent, parse_event
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from control_plane_api.deletion import handle_deletion_requested
from control_plane_api.models import EventRow
from control_plane_api.projections import project

IngestStatus = Literal["stored", "duplicate", "rejected"]


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    status: IngestStatus
    event: DoorboardEvent | None
    event_id: str | None
    error: str | None = None


def _extract_person_id(event: DoorboardEvent) -> str | None:
    return getattr(event.payload, "person_id", None)


def ingest_one(
    session: Session, raw: dict, *, batch_id: str | None, now: datetime
) -> IngestOutcome:
    try:
        event = parse_event(raw)
    except ValidationError as exc:
        return IngestOutcome(
            status="rejected", event=None, event_id=raw.get("event_id"), error=str(exc)
        )

    row = EventRow(
        event_id=str(event.event_id),
        type=event.type,
        source=event.source,
        occurred_at=event.occurred_at,
        monotonic_ms=event.monotonic_ms,
        door_id=event.door_id,
        trace_id=str(event.trace_id),
        payload=event.model_dump(mode="json")["payload"],
        person_id=_extract_person_id(event),
        batch_id=batch_id,
        received_at=now,
    )

    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        return IngestOutcome(status="duplicate", event=event, event_id=str(event.event_id))

    project(session, event, now=now)
    if event.type == "social.deletion_requested":
        handle_deletion_requested(session, event.payload, now=now)

    return IngestOutcome(status="stored", event=event, event_id=str(event.event_id))
