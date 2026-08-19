"""door-media's storage telemetry has to actually reach door-api.

door-media emits `media.storage_status` every 30 s, door-ui *subscribes* to it, and nothing
carried it between them. door-api forwards session events **to** door-media and nothing comes
back; door-media's own events went onto an in-process queue whose only reader is an
admin-authenticated SSE stream nothing consumes. So the wallboard's capacity card sat on
"Waiting for a media.storage_status update; no capacity is being guessed." permanently — honest
and permanently uninformative. Same shape as the greeting bug: emitted, never delivered.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from door_media.emitter import emit_storage_status
from door_media.event_forwarder import FORWARDED_EVENT_TYPES, EventForwarder
from doorboard_contracts.events import DoorboardEvent


class _Recorder:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.sent: list[DoorboardEvent] = []
        self.fail_times = fail_times
        self.attempts = 0

    def send(self, event: DoorboardEvent) -> None:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            msg = "door-api unreachable"
            raise RuntimeError(msg)
        self.sent.append(event)


async def _drain(forwarder: EventForwarder, expected: int, timeout_s: float = 2.0) -> None:
    """Wait for the forwarder to have processed `expected` events, or give up."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if forwarder.forwarded + forwarder.dropped + forwarder.failures >= expected:
            return
        await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_a_storage_status_event_reaches_door_api() -> None:
    transport = _Recorder()
    forwarder = EventForwarder(transport)
    await forwarder.start()
    try:
        emit_storage_status(
            free_bytes=42_000_000_000,
            queue_depth=3,
            oldest_unsynced_s=120,
            recording_allowed=True,
            trace_id=uuid.uuid4(),
            door_id="primary",
        )
        await _drain(forwarder, 1)
    finally:
        await forwarder.stop()

    assert forwarder.forwarded == 1
    assert len(transport.sent) == 1
    event = transport.sent[0]
    assert event.type == "media.storage_status"
    assert event.payload.free_bytes == 42_000_000_000
    assert event.payload.recording_allowed is True


@pytest.mark.anyio
async def test_only_storage_status_is_forwarded() -> None:
    # Recording lifecycle events assert that durable state exists; that belongs in the sync
    # path to the NUC, not a fan-out to a screen.
    assert {"media.storage_status"} == FORWARDED_EVENT_TYPES

    transport = _Recorder()
    forwarder = EventForwarder(transport)
    await forwarder.start()
    try:
        from door_media.emitter import emit_retention_deleted

        emit_retention_deleted(
            recording_id=__import__("uuid").uuid4(),
            reason="space",
            trace_id=uuid.uuid4(),
            door_id="primary",
        )
        await asyncio.sleep(0.15)
    finally:
        await forwarder.stop()

    assert transport.sent == []


@pytest.mark.anyio
async def test_an_unreachable_door_api_does_not_block_or_raise() -> None:
    """Recording and retention must never wait on the screen path."""
    transport = _Recorder(fail_times=99)
    forwarder = EventForwarder(transport, retry_backoff_s=10.0)
    await forwarder.start()
    try:
        for _ in range(3):
            emit_storage_status(
                free_bytes=1,
                queue_depth=0,
                oldest_unsynced_s=0,
                recording_allowed=False,
                trace_id=uuid.uuid4(),
                door_id="primary",
            )
        await _drain(forwarder, 3)
    finally:
        await forwarder.stop()

    assert forwarder.forwarded == 0
    assert forwarder.failures >= 1
    # The backoff window means an unreachable door-api costs one timeout per window, not one
    # per event — the later events are dropped without an attempt.
    assert transport.attempts < 3
    assert forwarder.dropped >= 1
    assert forwarder.last_error is not None


@pytest.mark.anyio
async def test_it_recovers_once_door_api_comes_back() -> None:
    transport = _Recorder(fail_times=1)
    forwarder = EventForwarder(transport, retry_backoff_s=0.05)
    await forwarder.start()
    try:
        emit_storage_status(
            free_bytes=1,
            queue_depth=0,
            oldest_unsynced_s=0,
            recording_allowed=False,
            trace_id=uuid.uuid4(),
            door_id="primary",
        )
        await _drain(forwarder, 1)
        await asyncio.sleep(0.12)  # let the quiet window lapse
        emit_storage_status(
            free_bytes=2,
            queue_depth=0,
            oldest_unsynced_s=0,
            recording_allowed=True,
            trace_id=uuid.uuid4(),
            door_id="primary",
        )
        await _drain(forwarder, 2)
    finally:
        await forwarder.stop()

    assert forwarder.forwarded == 1, "the second event should have got through"
    assert transport.sent[-1].payload.recording_allowed is True
