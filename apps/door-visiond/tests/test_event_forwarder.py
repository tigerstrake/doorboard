"""The hop that was missing: identity events leaving door-visiond's process.

Recognition worked and the screen stayed silent because ``EventEmitter`` pushed onto
an in-process queue that nothing drained (see :mod:`door_visiond.event_forwarder`).
The forwarder must ship identity events, must not ship frame-rate traffic, and must
never let a slow or dead door-api reach back into the recognition loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from door_visiond.clock import SystemClock
from door_visiond.embedder import MockEmbedder
from door_visiond.enrollment import ProfileSpec
from door_visiond.event_forwarder import EventForwarder, HttpEventTransport
from door_visiond.events import (
    get_broadcast_queue,
    make_face_visible,
    make_identity_expired,
    make_identity_stable,
    reset_broadcast_queue,
)
from door_visiond.pipeline import ScriptedBackend
from door_visiond.service import VisiondService
from door_visiond.settings import Settings
from doorboard_contracts.events import DoorboardEvent

from .conftest import CONSENT_VERSION, TEST_DIM, face


class _RecordingTransport:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[DoorboardEvent] = []
        self.attempts = 0
        self.fail = fail

    def send(self, event: DoorboardEvent) -> None:
        self.attempts += 1
        if self.fail:
            msg = "door-api unreachable"
            raise OSError(msg)
        self.sent.append(event)


def _identity_stable() -> DoorboardEvent:
    return make_identity_stable(
        clock=SystemClock(),
        door_id="primary",
        trace_id=uuid4(),
        person_id="prs_1",
        display_name="Tiger",
        consent_version="v1",
        confidence=0.91,
        expires_at=datetime.now(UTC),
        expires_at_monotonic_ms=1234,
        profile_id="sunrise",
    )


@pytest.fixture(autouse=True)
def _fresh_queue() -> Iterator[None]:
    """The broadcast queue is a module global bound to whichever loop first used it."""
    reset_broadcast_queue()
    yield
    reset_broadcast_queue()


async def _settle(queue: asyncio.Queue[DoorboardEvent]) -> None:
    await asyncio.wait_for(queue.join(), timeout=2.0)


@pytest.mark.anyio
async def test_identity_events_reach_door_api() -> None:
    transport = _RecordingTransport()
    forwarder = EventForwarder(transport)
    await forwarder.start()
    queue = get_broadcast_queue()

    queue.put_nowait(_identity_stable())
    queue.put_nowait(
        make_identity_expired(
            clock=SystemClock(), door_id="primary", trace_id=uuid4(), person_id="prs_1"
        )
    )
    await _settle(queue)
    await forwarder.stop()

    assert [event.type for event in transport.sent] == [
        "vision.identity_stable",
        "vision.identity_expired",
    ]
    assert forwarder.forwarded == 2


@pytest.mark.anyio
async def test_face_visible_is_not_forwarded() -> None:
    """It fires on every frame holding a face and no consumer reads it."""
    transport = _RecordingTransport()
    forwarder = EventForwarder(transport)
    await forwarder.start()
    queue = get_broadcast_queue()

    for _ in range(5):
        queue.put_nowait(
            make_face_visible(
                clock=SystemClock(),
                door_id="primary",
                trace_id=uuid4(),
                face_count=1,
                largest_face_px=140,
            )
        )
    await _settle(queue)
    await forwarder.stop()

    assert transport.sent == []
    assert transport.attempts == 0


@pytest.mark.anyio
async def test_an_unreachable_door_api_costs_one_attempt_per_backoff_window() -> None:
    """A dead screen path must not turn into a send attempt per recognition event.

    Each attempt blocks a worker thread for the HTTP timeout, so retrying every event
    would let door-api's downtime meter out the recognition loop's queue drain.
    """
    transport = _RecordingTransport(fail=True)
    forwarder = EventForwarder(transport, retry_backoff_s=30.0)
    await forwarder.start()
    queue = get_broadcast_queue()

    for _ in range(4):
        queue.put_nowait(_identity_stable())
    await _settle(queue)
    await forwarder.stop()

    assert transport.attempts == 1
    assert forwarder.failures == 1
    assert forwarder.dropped == 3
    assert forwarder.last_error is not None


@pytest.mark.anyio
async def test_a_failure_never_escapes_into_the_recognition_loop() -> None:
    """The forwarder task has to survive its own transport dying."""
    transport = _RecordingTransport(fail=True)
    forwarder = EventForwarder(transport, retry_backoff_s=0.01)
    await forwarder.start()
    queue = get_broadcast_queue()

    queue.put_nowait(_identity_stable())
    await _settle(queue)
    transport.fail = False
    await asyncio.sleep(0.05)  # let the backoff window lapse
    queue.put_nowait(_identity_stable())
    await _settle(queue)
    await forwarder.stop()

    assert forwarder.forwarded == 1  # recovered without a restart
    assert [event.type for event in transport.sent] == ["vision.identity_stable"]


@pytest.mark.anyio
async def test_a_recognised_person_reaches_door_api_end_to_end(ssd_settings: Settings) -> None:
    """The user-visible bug, from enrolled face to the event door-api needs.

    Everything upstream of this already worked and was tested; what nobody tested was
    whether the resulting event ever left the process. It did not, so the door greeted
    people on the ESP32 light and never on the screen.
    """
    transport = _RecordingTransport()
    embedding, _ = MockEmbedder(dim=TEST_DIM).embed(b"tiger-enrollment-photo")
    svc = VisiondService(
        ssd_settings,
        backend=ScriptedBackend([face(embedding)], interval_ms=1),
        event_forwarder=EventForwarder(transport),
    )
    svc.startup()
    svc.enroll(
        display_name="Tiger",
        consent_version=CONSENT_VERSION,
        consent_confirmed=True,
        images=[b"tiger-enrollment-photo"],
        profile=ProfileSpec(profile_id="sunrise", color="#f0a", sound=None),
    )

    await svc.start()
    try:
        for _ in range(200):
            if transport.sent:
                break
            await asyncio.sleep(0.01)
        forwarding_status = svc.health()["event_forwarding"]
    finally:
        await svc.stop()

    assert transport.sent, "an enrolled face produced no event for door-api"
    forwarded = transport.sent[0]
    assert forwarded.type == "vision.identity_stable"
    assert forwarded.payload.display_name == "Tiger"
    assert forwarding_status == "ok"


def test_transport_targets_door_apis_ingest_route() -> None:
    transport = HttpEventTransport(base_url="http://127.0.0.1:8080/", token="t")

    assert transport._url == "http://127.0.0.1:8080/internal/events"
