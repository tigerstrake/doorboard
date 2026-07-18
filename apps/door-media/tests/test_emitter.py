from __future__ import annotations

from uuid import uuid4

from door_media.emitter import (
    emit_storage_status,
    subscribe_broadcast_queue,
    unsubscribe_broadcast_queue,
)


def test_each_subscriber_receives_the_same_event() -> None:
    first = subscribe_broadcast_queue()
    second = subscribe_broadcast_queue()
    try:
        emitted = emit_storage_status(
            free_bytes=100,
            queue_depth=2,
            oldest_unsynced_s=3,
            recording_allowed=True,
            trace_id=uuid4(),
        )
        assert first.get_nowait() == emitted
        assert second.get_nowait() == emitted
    finally:
        unsubscribe_broadcast_queue(first)
        unsubscribe_broadcast_queue(second)
