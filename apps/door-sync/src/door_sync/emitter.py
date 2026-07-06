"""Structured-JSON emission for the ``sync.*`` event catalog.

The three sync events (``sync.upload_queued`` / ``_completed`` / ``_failed``)
are media-specific — their payloads carry ``recording_id`` — so they are only
emitted for clip/thumbnail items, never for event-mirror or purge items. They
are local observability signals: logged as structured JSON and pushed to an
in-process broadcast queue for the admin UI. They are deliberately **not**
mirrored back to the NUC (that would recurse: an event about queuing an event).

All UUIDs are UUIDv7; ``monotonic_ms`` comes from ``time.monotonic_ns()``;
timestamps are UTC.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from doorboard_contracts.events import (
    SyncUploadCompletedEvent,
    SyncUploadCompletedPayload,
    SyncUploadFailedEvent,
    SyncUploadFailedPayload,
    SyncUploadQueuedEvent,
    SyncUploadQueuedPayload,
)

from door_sync._uuid7 import uuid7

logger = logging.getLogger("door_sync.events")

_EVENT_QUEUE_SIZE = 256
_broadcast_queue: asyncio.Queue[Any] | None = None


def get_broadcast_queue() -> asyncio.Queue[Any]:
    global _broadcast_queue  # noqa: PLW0603
    if _broadcast_queue is None:
        _broadcast_queue = asyncio.Queue(maxsize=_EVENT_QUEUE_SIZE)
    return _broadcast_queue


def reset_broadcast_queue() -> None:
    """Test hook — drop the module-level queue so a new event loop gets a fresh one."""
    global _broadcast_queue  # noqa: PLW0603
    _broadcast_queue = None


def _mono_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _emit(event: Any, door_id: str) -> None:
    data = event.model_dump(mode="json")
    logger.info(
        "event",
        extra={
            "service": "door-sync",
            "event_type": event.type,
            "event_id": str(event.event_id),
            "trace_id": str(event.trace_id),
            "door_id": door_id,
            "payload": data["payload"],
        },
    )
    try:
        q = get_broadcast_queue()
    except RuntimeError:
        return  # no running loop (e.g. unit test without an app) — logging is enough
    if q.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            q.get_nowait()
    with contextlib.suppress(asyncio.QueueFull):
        q.put_nowait(event)


def emit_upload_queued(
    *, item_id: UUID, recording_id: UUID, target: str, trace_id: UUID, door_id: str
) -> SyncUploadQueuedEvent:
    ev = SyncUploadQueuedEvent(
        event_id=uuid7(),
        type="sync.upload_queued",
        source="door-sync",
        occurred_at=datetime.now(UTC),
        monotonic_ms=_mono_ms(),
        door_id=door_id,
        trace_id=trace_id,
        payload=SyncUploadQueuedPayload(
            item_id=item_id,
            recording_id=recording_id,
            target=target,  # type: ignore[arg-type]
        ),
    )
    _emit(ev, door_id)
    return ev


def emit_upload_completed(
    *, item_id: UUID, verified_sha256: str, attempts: int, trace_id: UUID, door_id: str
) -> SyncUploadCompletedEvent:
    ev = SyncUploadCompletedEvent(
        event_id=uuid7(),
        type="sync.upload_completed",
        source="door-sync",
        occurred_at=datetime.now(UTC),
        monotonic_ms=_mono_ms(),
        door_id=door_id,
        trace_id=trace_id,
        payload=SyncUploadCompletedPayload(
            item_id=item_id,
            verified_sha256=verified_sha256,
            attempts=attempts,
        ),
    )
    _emit(ev, door_id)
    return ev


def emit_upload_failed(
    *,
    item_id: UUID,
    attempts: int,
    next_retry_at: datetime,
    error_class: str,
    trace_id: UUID,
    door_id: str,
) -> SyncUploadFailedEvent:
    ev = SyncUploadFailedEvent(
        event_id=uuid7(),
        type="sync.upload_failed",
        source="door-sync",
        occurred_at=datetime.now(UTC),
        monotonic_ms=_mono_ms(),
        door_id=door_id,
        trace_id=trace_id,
        payload=SyncUploadFailedPayload(
            item_id=item_id,
            attempts=attempts,
            next_retry_at=next_retry_at,
            error_class=error_class,
        ),
    )
    _emit(ev, door_id)
    return ev
