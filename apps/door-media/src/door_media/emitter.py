"""Structured-JSON event emission for door-media.

Every outbound event is logged and broadcast over the local WebSocket hub
(same process, no network hop needed for in-process consumers).  External
consumers subscribe via ``GET /events`` (SSE) or the WebSocket hub.

All UUIDs emitted here are UUIDv7; ``monotonic_ms`` is always from
``time.monotonic_ns()``.  Timestamps are always UTC (``datetime.now(UTC)``).
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
    MediaRecordingFinalizedEvent,
    MediaRecordingFinalizedPayload,
    MediaRecordingStartedEvent,
    MediaRecordingStartedPayload,
    MediaRetentionDeletedEvent,
    MediaRetentionDeletedPayload,
    MediaStorageStatusEvent,
    MediaStorageStatusPayload,
    MediaThumbnailReadyEvent,
    MediaThumbnailReadyPayload,
    SystemStorageAlertEvent,
    SystemStorageAlertPayload,
)

from door_media._uuid7 import uuid7

logger = logging.getLogger("door_media.events")

# ---------------------------------------------------------------------------
# Module-level broadcast queue: anyone can await emitted events.
# The queue is intentionally bounded; a full queue drops the oldest rather
# than blocking the recording path.
# ---------------------------------------------------------------------------
_EVENT_QUEUE_SIZE = 256
_broadcast_queue: asyncio.Queue[Any] | None = None
_subscribers: set[asyncio.Queue[Any]] = set()


def get_broadcast_queue() -> asyncio.Queue[Any]:
    global _broadcast_queue  # noqa: PLW0603
    if _broadcast_queue is None:
        _broadcast_queue = asyncio.Queue(maxsize=_EVENT_QUEUE_SIZE)
    return _broadcast_queue


def subscribe_broadcast_queue() -> asyncio.Queue[Any]:
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_EVENT_QUEUE_SIZE)
    _subscribers.add(queue)
    return queue


def unsubscribe_broadcast_queue(queue: asyncio.Queue[Any]) -> None:
    _subscribers.discard(queue)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _mono_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _source() -> str:
    return "door-media"


def _door_id(door_id: str = "primary") -> str:
    return door_id


def _emit(event: Any, door_id: str = "primary") -> None:
    """Log the event as structured JSON and push to the broadcast queue."""
    data = event.model_dump(mode="json")
    logger.info(
        "event",
        extra={
            "service": "door-media",
            "event_type": event.type,
            "event_id": str(event.event_id),
            "trace_id": str(event.trace_id),
            "door_id": door_id,
            "payload": data["payload"],
        },
    )
    queues = (get_broadcast_queue(), *_subscribers)
    for queue in queues:
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(event)


# ---------------------------------------------------------------------------
# Emitters — one per event type from the media.* and system.* catalogs
# ---------------------------------------------------------------------------


def emit_recording_started(
    *,
    recording_id: UUID,
    session_id: UUID,
    kind: str,
    stream: str,
    trace_id: UUID,
    door_id: str = "primary",
) -> MediaRecordingStartedEvent:
    ev = MediaRecordingStartedEvent(
        event_id=uuid7(),
        type="media.recording_started",
        source=_source(),
        occurred_at=_now_utc(),
        monotonic_ms=_mono_ms(),
        door_id=door_id,
        trace_id=trace_id,
        payload=MediaRecordingStartedPayload(
            recording_id=recording_id,
            session_id=session_id,
            kind=kind,  # type: ignore[arg-type]
            stream=stream,
        ),
    )
    _emit(ev, door_id)
    return ev


def emit_recording_finalized(
    *,
    recording_id: UUID,
    path: str,
    duration_s: float,
    size_bytes: int,
    sha256: str,
    consent_context: str,
    trace_id: UUID,
    door_id: str = "primary",
) -> MediaRecordingFinalizedEvent:
    ev = MediaRecordingFinalizedEvent(
        event_id=uuid7(),
        type="media.recording_finalized",
        source=_source(),
        occurred_at=_now_utc(),
        monotonic_ms=_mono_ms(),
        door_id=door_id,
        trace_id=trace_id,
        payload=MediaRecordingFinalizedPayload(
            recording_id=recording_id,
            path=path,
            duration_s=duration_s,
            size_bytes=size_bytes,
            sha256=sha256,
            consent_context=consent_context,  # type: ignore[arg-type]
        ),
    )
    _emit(ev, door_id)
    return ev


def emit_thumbnail_ready(
    *,
    recording_id: UUID,
    path: str,
    trace_id: UUID,
    door_id: str = "primary",
) -> MediaThumbnailReadyEvent:
    ev = MediaThumbnailReadyEvent(
        event_id=uuid7(),
        type="media.thumbnail_ready",
        source=_source(),
        occurred_at=_now_utc(),
        monotonic_ms=_mono_ms(),
        door_id=door_id,
        trace_id=trace_id,
        payload=MediaThumbnailReadyPayload(
            recording_id=recording_id,
            path=path,
        ),
    )
    _emit(ev, door_id)
    return ev


def emit_retention_deleted(
    *,
    recording_id: UUID,
    reason: str,
    trace_id: UUID,
    door_id: str = "primary",
) -> MediaRetentionDeletedEvent:
    ev = MediaRetentionDeletedEvent(
        event_id=uuid7(),
        type="media.retention_deleted",
        source=_source(),
        occurred_at=_now_utc(),
        monotonic_ms=_mono_ms(),
        door_id=door_id,
        trace_id=trace_id,
        payload=MediaRetentionDeletedPayload(
            recording_id=recording_id,
            reason=reason,  # type: ignore[arg-type]
        ),
    )
    _emit(ev, door_id)
    return ev


def emit_storage_status(
    *,
    free_bytes: int,
    queue_depth: int,
    oldest_unsynced_s: int,
    recording_allowed: bool,
    trace_id: UUID,
    door_id: str = "primary",
) -> MediaStorageStatusEvent:
    ev = MediaStorageStatusEvent(
        event_id=uuid7(),
        type="media.storage_status",
        source=_source(),
        occurred_at=_now_utc(),
        monotonic_ms=_mono_ms(),
        door_id=door_id,
        trace_id=trace_id,
        payload=MediaStorageStatusPayload(
            free_bytes=free_bytes,
            queue_depth=queue_depth,
            oldest_unsynced_s=oldest_unsynced_s,
            recording_allowed=recording_allowed,
        ),
    )
    _emit(ev, door_id)
    return ev


def emit_storage_alert(
    *,
    host: str,
    mount: str,
    free_bytes: int,
    severity: str,
    trace_id: UUID,
    door_id: str = "primary",
) -> SystemStorageAlertEvent:
    ev = SystemStorageAlertEvent(
        event_id=uuid7(),
        type="system.storage_alert",
        source=_source(),
        occurred_at=_now_utc(),
        monotonic_ms=_mono_ms(),
        door_id=door_id,
        trace_id=trace_id,
        payload=SystemStorageAlertPayload(
            host=host,
            mount=mount,
            free_bytes=free_bytes,
            severity=severity,  # type: ignore[arg-type]
        ),
    )
    _emit(ev, door_id)
    return ev
