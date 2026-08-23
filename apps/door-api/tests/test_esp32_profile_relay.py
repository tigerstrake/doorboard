"""door-api relays the recognition profile push to the ESP32 it owns (ADR-0040).

door-visiond computes the profile + its expiry but cannot reach the controller — the
single ESP32 UART belongs to door-api. So the proactive ``door.profile_update`` /
``door.profile_clear`` is POSTed to ``/internal/esp32/profile`` and door-api puts it on
the wire. Before this the light leg was dead: door-visiond had nowhere to send it and
every recognised person got the on-screen greeting but no personalized light.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Generator, Mapping
from typing import Any

import pytest
from doorboard_contracts.examples import example_event
from doorboard_esp32_link import Esp32TransportStatus, WireMessage, wire_message_from_event
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state  # noqa: E402

TOKEN = "internal-event-token-for-tests"


class _RecordingTransport:
    """An ESP32 transport that records what door-api relays, allocating real seqs."""

    def __init__(self) -> None:
        self.sent_events: list[str] = []
        self.sent_messages: list[WireMessage] = []
        self._seq = 0
        self.fail = False

    def make_message(self, message_type: str, payload: Mapping[str, object]) -> WireMessage:
        self._seq += 1
        return WireMessage(v=1, seq=self._seq, message_type=message_type, ack=None, payload=payload)

    async def send_event(self, event: Any) -> WireMessage:
        if self.fail:
            raise RuntimeError("esp32 offline")
        self._seq += 1
        msg = wire_message_from_event(event, seq=self._seq, now_mono_ms=0)
        self.sent_events.append(event.type)
        self.sent_messages.append(msg)
        return WireMessage(v=1, seq=0, message_type="ack", ack=msg.seq, payload={})

    async def send(self, msg: WireMessage) -> WireMessage:
        return WireMessage(v=1, seq=0, message_type="ack", ack=msg.seq, payload={})

    def events(self) -> AsyncIterator[Any]:
        raise NotImplementedError

    def status(self) -> Esp32TransportStatus:
        return Esp32TransportStatus(
            connected=True, last_heartbeat_mono_ms=None, rx_errors=0, tx_retries=0
        )


@pytest.fixture(autouse=True)
def _app_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_INTERNAL_EVENT_TOKEN", TOKEN)
    state.__init__()
    state.startup()
    yield
    state.shutdown()


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def _profile_update() -> dict[str, Any]:
    return example_event("door.profile_update").model_dump(mode="json")


def _profile_clear() -> dict[str, Any]:
    return example_event("door.profile_clear").model_dump(mode="json")


def test_profile_update_is_relayed_to_the_esp32() -> None:
    transport = _RecordingTransport()
    state.attach_esp32_transport(transport)
    try:
        client = TestClient(app)
        resp = client.post("/internal/esp32/profile", json=_profile_update(), headers=_auth())
    finally:
        state.detach_esp32_transport()

    assert resp.status_code == 202
    assert resp.json()["status"] == "sent"
    assert transport.sent_events == ["door.profile_update"]
    # A real seq off the transport's own counter, never the seq=0 sentinel.
    assert transport.sent_messages[0].seq != 0
    assert state.esp32_profile_relayed == 1


def test_profile_clear_is_relayed_to_the_esp32() -> None:
    transport = _RecordingTransport()
    state.attach_esp32_transport(transport)
    try:
        client = TestClient(app)
        resp = client.post("/internal/esp32/profile", json=_profile_clear(), headers=_auth())
    finally:
        state.detach_esp32_transport()

    assert resp.status_code == 202
    assert transport.sent_events == ["door.profile_clear"]


def test_route_refuses_anything_but_a_profile_push() -> None:
    # It is the mirror image of /internal/events: that route refuses door.* inbound
    # inputs; this one refuses everything EXCEPT the two outbound profile events, so it
    # can never be used to inject an identity, a session change, or a button press.
    transport = _RecordingTransport()
    state.attach_esp32_transport(transport)
    try:
        client = TestClient(app)
        resp = client.post(
            "/internal/esp32/profile",
            json=example_event("vision.identity_stable").model_dump(mode="json"),
            headers=_auth(),
        )
    finally:
        state.detach_esp32_transport()

    assert resp.status_code == 403
    assert transport.sent_events == []


def test_route_requires_the_internal_token() -> None:
    client = TestClient(app)
    resp = client.post("/internal/esp32/profile", json=_profile_update())
    assert resp.status_code == 401


def test_a_missing_esp32_is_a_recorded_no_op_not_an_error() -> None:
    # The personalized light is a convenience, never the critical path: a door with no
    # ESP32 (or a mock one) must accept the push and record it did nothing, not 500.
    assert state.esp32_transport is None
    client = TestClient(app)
    resp = client.post("/internal/esp32/profile", json=_profile_update(), headers=_auth())
    assert resp.status_code == 202
    assert resp.json()["status"] == "unavailable"
    assert state.esp32_profile_relay_noop == 1


def test_an_offline_controller_is_reported_not_raised() -> None:
    transport = _RecordingTransport()
    transport.fail = True
    state.attach_esp32_transport(transport)
    try:
        client = TestClient(app)
        resp = client.post("/internal/esp32/profile", json=_profile_update(), headers=_auth())
    finally:
        state.detach_esp32_transport()

    assert resp.status_code == 202
    assert resp.json()["status"] == "failed"
    assert state.esp32_profile_relay_failed == 1
