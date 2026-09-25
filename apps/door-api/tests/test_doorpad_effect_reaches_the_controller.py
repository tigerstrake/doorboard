"""The DoorPad ring has to actually reach the LEDs.

Both bugs pinned here were silent: `play_doorpad_effect` returned
``{"status": "sent"}`` in each case, so every log line and metric said the door had
given feedback while the hardware sat dark. ARCHITECTURE.md §10 is explicit that the
Pi must never pretend a physical effect occurred, and "the transport acked it" is not
evidence that it did.

**Sequence numbers.** The outbound message was hand-built with ``seq=0`` every time.
The controller dedupes inbound frames by ``(boot_id, seq)`` in a 16-entry ring and
acks duplicates *before* discarding them, so the first ring after boot played and
every ring after that was thrown away — the common case being a visitor tapping
twice. Only the transport can allocate a correct `seq`, so the message must come from
its factory.

**Effect id.** The configured default named an effect the firmware does not have.
`door_effect_from_name` returns ``DOOR_EFFECT_NONE`` for an unknown name and
`on_effect_play_received` drops the message, so the ring produced nothing at all.
The name is checked against the firmware's own table rather than a copy of it.

**The route never waits for it.** ``/doorpad/ring`` and the video-message offer used
to await the ack before answering — four 50 ms attempts with a silent controller,
212 ms measured against a 100 ms tap budget (ARCHITECTURE.md §4). The send now runs as
a task; the response carries ``requested`` and the outcome is a metric and a log line.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncIterator, Generator, Mapping
from pathlib import Path

import pytest
from doorboard_contracts.events import DoorboardEvent
from doorboard_esp32_link import Esp32TransportStatus, WireMessage
from fastapi.testclient import TestClient

# Importing door_api.app builds the module-level state, which needs a database
# before it can be constructed. Same preamble as test_esp32_link.py.
os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    # Other modules close the shared store at teardown; rebuild it for each test here.
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    state.__init__()
    state.startup()
    yield
    state.shutdown()


REPO_ROOT = Path(__file__).resolve().parents[3]
EFFECTS_C = (
    REPO_ROOT
    / "firmware"
    / "esp32-door-controller"
    / "components"
    / "door_effects"
    / "door_effects.c"
)


class _NoFrames:
    """An inbound stream that stays open and carries nothing: no controller frames here."""

    def __aiter__(self) -> _NoFrames:
        return self

    async def __anext__(self) -> DoorboardEvent:
        await asyncio.Event().wait()
        raise StopAsyncIteration


class _RecordingTransport:
    """A transport that allocates sequence numbers the way the real one does."""

    def __init__(self) -> None:
        self.sent: list[WireMessage] = []
        self._seq = 0

    def make_message(self, message_type: str, payload: Mapping[str, object]) -> WireMessage:
        self._seq += 1
        return WireMessage(v=1, seq=self._seq, message_type=message_type, ack=None, payload=payload)

    async def send(self, msg: WireMessage) -> WireMessage:
        self.sent.append(msg)
        return WireMessage(v=1, seq=0, message_type="ack", ack=msg.seq, payload={})

    async def send_event(self, event: DoorboardEvent) -> WireMessage:
        raise NotImplementedError

    def events(self) -> AsyncIterator[DoorboardEvent]:
        return _NoFrames()

    def status(self) -> Esp32TransportStatus:
        return Esp32TransportStatus(
            connected=True, last_heartbeat_mono_ms=None, rx_errors=0, tx_retries=0
        )


class _SilentTransport(_RecordingTransport):
    """Acks nothing — a controller mid-reboot, or a cable that fell out."""

    async def send(self, msg: WireMessage) -> WireMessage:
        self.sent.append(msg)
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


class _FailingTransport(_RecordingTransport):
    """The transport gave up on the ack (the real one raises AckTimeoutError)."""

    async def send(self, msg: WireMessage) -> WireMessage:
        self.sent.append(msg)
        raise TimeoutError("no ack")


def _firmware_effect_names() -> set[str]:
    """The names `door_effect_from_name` actually accepts, read from the firmware."""
    source = EFFECTS_C.read_text(encoding="utf-8")
    body = source.split("door_effect_id_t door_effect_from_name", 1)[1].split("\n}", 1)[0]
    return set(re.findall(r'strcmp\(name,\s*"([^"]+)"\)', body))


def test_repeated_rings_get_fresh_sequence_numbers() -> None:
    """A double-tap must reach the controller twice, not be deduped into one."""
    transport = _RecordingTransport()
    state.attach_esp32_transport(transport)
    try:
        results = asyncio.run(_ring_twice())
    finally:
        state.detach_esp32_transport()

    assert results == [{"status": "sent"}, {"status": "sent"}]
    assert [msg.message_type for msg in transport.sent] == ["effect_play", "effect_play"]

    seqs = [msg.seq for msg in transport.sent]
    assert len(set(seqs)) == 2, f"repeated rings reused a sequence number: {seqs}"
    assert seqs[1] > seqs[0], f"sequence numbers must advance, got {seqs}"
    assert 0 not in seqs, "seq 0 is the hand-built sentinel this test exists to prevent"


async def _ring_twice() -> list[dict[str, str]]:
    return [await state.play_doorpad_effect(), await state.play_doorpad_effect()]


@pytest.mark.parametrize("route", ["/doorpad/ring", "/doorpad/video-message/offer"])
def test_a_doorpad_route_answers_without_waiting_for_the_controller(route: str) -> None:
    transport = _SilentTransport()
    client = TestClient(app)
    client.get("/health")  # warm the test client, so the timing below is the route's
    state.attach_esp32_transport(transport)
    try:
        started = time.perf_counter()
        response = client.post(route)
        elapsed_ms = (time.perf_counter() - started) * 1000
    finally:
        state.detach_esp32_transport()

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["effect"] == {"status": "requested"}
    assert elapsed_ms < 50, f"{route} took {elapsed_ms:.0f} ms with a silent controller"
    assert [msg.message_type for msg in transport.sent] == ["effect_play"], (
        "the effect was still put on the wire"
    )


@pytest.mark.anyio
async def test_the_effect_outcome_is_recorded_off_the_request_path() -> None:
    state.effect_sent = state.effect_failed = 0
    acking = _RecordingTransport()
    state.attach_esp32_transport(acking)
    try:
        assert state.request_doorpad_effect() == {"status": "requested"}
        for _ in range(3):
            await asyncio.sleep(0)
        assert [msg.message_type for msg in acking.sent] == ["effect_play"]
        assert state.effect_sent == 1
        assert state.effect_failed == 0
    finally:
        state.detach_esp32_transport()

    failing = _FailingTransport()
    state.attach_esp32_transport(failing)
    try:
        assert state.request_doorpad_effect() == {"status": "requested"}
        for _ in range(3):
            await asyncio.sleep(0)
        assert state.effect_failed == 1, "a controller that never acks is counted, not hidden"
    finally:
        state.detach_esp32_transport()

    metrics = TestClient(app).get("/metrics").text
    assert "door_api_doorpad_effect_sent_total 1" in metrics
    assert "door_api_doorpad_effect_failed_total 1" in metrics


def test_no_controller_is_still_reported_as_unavailable() -> None:
    # The one outcome known at request time stays in the response: nothing is attached.
    assert state.esp32_transport is None
    assert TestClient(app).post("/doorpad/ring").json()["effect"] == {"status": "unavailable"}


def test_configured_doorpad_effect_exists_in_the_firmware() -> None:
    """An effect id the firmware cannot resolve is acked and then silently dropped."""
    names = _firmware_effect_names()
    assert "generic_press" in names, "sanity: the firmware table was parsed"
    assert state.config.doorpad_effect_id in names, (
        f"doorpad_effect_id {state.config.doorpad_effect_id!r} is not a firmware effect; "
        f"door_effect_from_name would return DOOR_EFFECT_NONE. Known: {sorted(names)}"
    )
