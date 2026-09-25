"""Core ingest logic: validate, idempotently store, and project one event.

Kept separate from the HTTP layer (routes.py) so it's testable without a
FastAPI app, and separate from notify/mqtt fan-out (service.py) so
duplicate-detection and mirror projection have no dependency on the
notifier/broker being reachable.

Outcomes: `stored`, `duplicate` (same event_id already archived), `rejected`
(the contract refused it, or the database did). A contract-valid event the
database refuses — a string longer than its mirror column, a value Postgres
cannot encode — used to escape as a 500; door-sync read that as transient and
burned its retry budget on an event that would never land. It is now a
`rejected` result with a sanitised message, and the rest of the batch is
unaffected. U+0000 is stripped from every string first: Postgres text cannot
hold it, and a NUL in a guestbook note is never meaningful content.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from doorboard_contracts import DoorboardEvent, parse_event
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from control_plane_api.deletion import handle_deletion_requested
from control_plane_api.models import EventRow
from control_plane_api.projections import project

logger = logging.getLogger("control_plane_api.ingest")

IngestStatus = Literal["stored", "duplicate", "rejected"]

# A rejection message is echoed back to the Pi in the batch result and logged;
# keep it short and free of the offending input.
_ERROR_MAX_CHARS = 512


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    status: IngestStatus
    event: DoorboardEvent | None
    event_id: str | None
    error: str | None = None


def _extract_person_id(event: DoorboardEvent) -> str | None:
    return getattr(event.payload, "person_id", None)


def strip_nul(value: Any) -> Any:
    """Recursively remove U+0000 from every string in a JSON-shaped value."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {key: strip_nul(item) for key, item in value.items()}
    if isinstance(value, list):
        return [strip_nul(item) for item in value]
    return value


def describe_validation_error(exc: ValidationError) -> str:
    """A rejection string that names the failing fields but never echoes the input.

    `str(exc)` includes pydantic's `input_value=...`, which sent whatever the
    Pi had put in the event — a visitor's name, a guestbook sentence — straight
    back over the wire and into the logs on both ends.
    """
    parts: list[str] = []
    for error in exc.errors(include_input=False, include_url=False):
        location = ".".join(str(item) for item in error.get("loc", ()))
        parts.append(
            f"{location}: {error.get('msg', 'invalid')}"
            if location
            else error.get("msg", "invalid")
        )
    text = "; ".join(parts) or "invalid event"
    return text[:_ERROR_MAX_CHARS]


def _describe_db_refusal(exc: StatementError) -> str:
    # The exception's own message carries the statement and its parameters, i.e.
    # the event content. Only the class (and Postgres' SQLSTATE) is reported.
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    suffix = f", sqlstate={sqlstate}" if sqlstate else ""
    return f"database refused the event ({type(exc).__name__}{suffix})"


def ingest_one(
    session: Session, raw: dict, *, batch_id: str | None, now: datetime
) -> IngestOutcome:
    raw = strip_nul(raw)
    try:
        event = parse_event(raw)
    except ValidationError as exc:
        return IngestOutcome(
            status="rejected",
            event=None,
            event_id=raw.get("event_id"),
            error=describe_validation_error(exc),
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

    # One savepoint around the insert AND the projections: if a mirror column
    # refuses the value the raw row must roll back with it, or the event would be
    # archived while reported as rejected (and never retried into the mirror).
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
            project(session, event, now=now)
            if event.type == "social.deletion_requested":
                handle_deletion_requested(session, event.payload, now=now)
            session.flush()
    except IntegrityError:
        return IngestOutcome(status="duplicate", event=event, event_id=str(event.event_id))
    except StatementError as exc:  # DataError, ProgrammingError, ... — the DB said no
        error = _describe_db_refusal(exc)
        logger.warning(
            "event_refused_by_database",
            extra={"event_id": str(event.event_id), "type": event.type, "error": error},
        )
        return IngestOutcome(
            status="rejected", event=event, event_id=str(event.event_id), error=error
        )

    return IngestOutcome(status="stored", event=event, event_id=str(event.event_id))
