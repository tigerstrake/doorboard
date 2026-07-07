"""Typed vision event builders + emitter (all messages use packages/contracts).

Builders construct the contract event models; :class:`EventEmitter` logs each as
structured JSON (the audit path) and pushes it to a bounded in-process
broadcast queue for local subscribers.  No embedding or frame ever appears in a
payload — the contract firewall (ADR-0009 E-4) makes that impossible, and the
E-3 redaction filter scrubs the structured log as defense in depth.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import Any
from uuid import UUID

from doorboard_contracts.events import (
    DoorboardEvent,
    DoorProfileClearEvent,
    DoorProfileClearPayload,
    DoorProfileUpdateEvent,
    DoorProfileUpdatePayload,
    VisionFaceVisibleEvent,
    VisionFaceVisiblePayload,
    VisionIdentityExpiredEvent,
    VisionIdentityExpiredPayload,
    VisionIdentityStableEvent,
    VisionIdentityStablePayload,
    VisionPipelineStatusEvent,
    VisionPipelineStatusPayload,
    VisionPrivacyModeChangedEvent,
    VisionPrivacyModeChangedPayload,
)

from door_visiond._uuid7 import uuid7
from door_visiond.clock import Clock
from door_visiond.logging_setup import get_logger

logger = get_logger("door_visiond.events")

SOURCE = "door-visiond"

_EVENT_QUEUE_SIZE = 256
_broadcast_queue: asyncio.Queue[DoorboardEvent] | None = None


def get_broadcast_queue() -> asyncio.Queue[DoorboardEvent]:
    global _broadcast_queue  # noqa: PLW0603
    if _broadcast_queue is None:
        _broadcast_queue = asyncio.Queue(maxsize=_EVENT_QUEUE_SIZE)
    return _broadcast_queue


def _base_fields(clock: Clock, door_id: str, trace_id: UUID) -> dict[str, Any]:
    return {
        "event_id": uuid7(),
        "source": SOURCE,
        "occurred_at": clock.utc_now(),
        "monotonic_ms": clock.monotonic_ms(),
        "door_id": door_id,
        "trace_id": trace_id,
    }


def make_face_visible(
    *, clock: Clock, door_id: str, trace_id: UUID, face_count: int, largest_face_px: int
) -> VisionFaceVisibleEvent:
    return VisionFaceVisibleEvent(
        type="vision.face_visible",
        payload=VisionFaceVisiblePayload(face_count=face_count, largest_face_px=largest_face_px),
        **_base_fields(clock, door_id, trace_id),
    )


def make_identity_stable(
    *,
    clock: Clock,
    door_id: str,
    trace_id: UUID,
    person_id: str,
    display_name: str,
    confidence: float,
    expires_at: datetime,
    expires_at_monotonic_ms: int,
    profile_id: str,
) -> VisionIdentityStableEvent:
    return VisionIdentityStableEvent(
        type="vision.identity_stable",
        payload=VisionIdentityStablePayload(
            person_id=person_id,
            display_name=display_name,
            confidence=confidence,
            expires_at=expires_at,
            expires_at_monotonic_ms=expires_at_monotonic_ms,
            profile_id=profile_id,
        ),
        **_base_fields(clock, door_id, trace_id),
    )


def make_identity_expired(
    *, clock: Clock, door_id: str, trace_id: UUID, person_id: str
) -> VisionIdentityExpiredEvent:
    return VisionIdentityExpiredEvent(
        type="vision.identity_expired",
        payload=VisionIdentityExpiredPayload(person_id=person_id),
        **_base_fields(clock, door_id, trace_id),
    )


def make_door_profile_update(
    *,
    clock: Clock,
    door_id: str,
    trace_id: UUID,
    profile_id: str,
    expires_at_monotonic_ms: int,
    priority: str,
) -> DoorProfileUpdateEvent:
    return DoorProfileUpdateEvent(
        type="door.profile_update",
        payload=DoorProfileUpdatePayload(
            profile_id=profile_id,
            expires_at_monotonic_ms=expires_at_monotonic_ms,
            priority=priority,  # type: ignore[arg-type]
        ),
        **_base_fields(clock, door_id, trace_id),
    )


def make_door_profile_clear(
    *, clock: Clock, door_id: str, trace_id: UUID, reason: str
) -> DoorProfileClearEvent:
    return DoorProfileClearEvent(
        type="door.profile_clear",
        payload=DoorProfileClearPayload(reason=reason),  # type: ignore[arg-type]
        **_base_fields(clock, door_id, trace_id),
    )


def make_privacy_mode_changed(
    *, clock: Clock, door_id: str, trace_id: UUID, enabled: bool, changed_by: str
) -> VisionPrivacyModeChangedEvent:
    return VisionPrivacyModeChangedEvent(
        type="vision.privacy_mode_changed",
        payload=VisionPrivacyModeChangedPayload(
            enabled=enabled,
            changed_by=changed_by,  # type: ignore[arg-type]
        ),
        **_base_fields(clock, door_id, trace_id),
    )


def make_pipeline_status(
    *,
    clock: Clock,
    door_id: str,
    trace_id: UUID,
    mode: str,
    hailo_ok: bool,
    fps: float,
    inference_ms_p50: float,
) -> VisionPipelineStatusEvent:
    return VisionPipelineStatusEvent(
        type="vision.pipeline_status",
        payload=VisionPipelineStatusPayload(
            mode=mode,  # type: ignore[arg-type]
            hailo_ok=hailo_ok,
            fps=fps,
            inference_ms_p50=inference_ms_p50,
        ),
        **_base_fields(clock, door_id, trace_id),
    )


class EventEmitter:
    """Logs an event as structured JSON and broadcasts it in-process."""

    def __init__(self, door_id: str = "primary") -> None:
        self._door_id = door_id

    def emit(self, event: DoorboardEvent) -> None:
        logger.info(
            "event",
            extra={
                "service": SOURCE,
                "event_type": event.type,
                "event_id": str(event.event_id),
                "trace_id": str(event.trace_id),
                "door_id": event.door_id,
                # Vision payloads are counts/ids/timestamps only — never biometric.
                "payload": event.payload.model_dump(mode="json"),
            },
        )
        q = get_broadcast_queue()
        if q.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(event)
