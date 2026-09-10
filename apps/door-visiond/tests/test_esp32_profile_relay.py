"""In production door-visiond has no ESP32 transport (door-api owns the single UART),
so the proactive profile push is relayed to door-api over loopback instead (ADR-0040).

These pin that the relay actually fires on a stable match — the personalized-light leg
that was dead before, because door-visiond built the push and had nowhere to send it —
that a same-profile refresh is throttled so it is not a loopback POST per frame, and
that the leg cannot *look* alive while doing nothing: door-api's 202 body is read, a
push it could not put on the wire is a counted failure, and a door with no ESP32 path
at all says so in /health.
"""

from __future__ import annotations

import io
import json
from typing import Any
from uuid import uuid4

import pytest
from door_visiond.clock import FakeClock
from door_visiond.embedder import MockEmbedder
from door_visiond.enrollment import ProfileSpec
from door_visiond.event_forwarder import HttpProfileTransport, ProfileRelayNotSent
from door_visiond.events import EventEmitter, make_door_profile_update
from door_visiond.service import VisiondService
from door_visiond.settings import Settings
from doorboard_contracts.events import DoorboardEvent, DoorProfileUpdateEvent

from .conftest import CONSENT_VERSION, TEST_DIM, face


class _CollectingEmitter(EventEmitter):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[DoorboardEvent] = []

    def emit(self, event: DoorboardEvent) -> None:
        self.events.append(event)


class _RecordingProfileRelay:
    """A door-api profile relay double: records the contract events it is handed."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.sent: list[DoorboardEvent] = []
        self.fail_with = fail_with

    def send(self, event: DoorboardEvent) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(event)


def _profile(profile_id: str = "blue_wave") -> ProfileSpec:
    return ProfileSpec(profile_id=profile_id, color="#0000ff", sound=None)


def _build(
    settings: Settings, clock: FakeClock, relay: _RecordingProfileRelay | None
) -> VisiondService:
    # No esp32_transport: this is the production shape, where the push must relay.
    svc = VisiondService(
        settings, clock=clock, emitter=_CollectingEmitter(), esp32_profile_relay=relay
    )
    svc.startup()
    svc.enroll(
        display_name="Alex",
        consent_version=CONSENT_VERSION,
        consent_confirmed=True,
        images=[b"alex-photo-bytes"],
        profile=_profile(),
    )
    return svc


def _emb():
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(b"alex-photo-bytes")
    return emb


def test_a_stable_match_relays_the_profile_update_to_door_api(ssd_settings: Settings) -> None:
    clock = FakeClock()
    relay = _RecordingProfileRelay()
    svc = _build(ssd_settings, clock, relay)

    emb = _emb()
    svc.core.process_capture(face(emb))
    svc.core.process_capture(face(emb))  # 2 of 3 → stable → first (high-priority) push

    assert [e.type for e in relay.sent] == ["door.profile_update"]
    pushed = relay.sent[0]
    assert isinstance(pushed, DoorProfileUpdateEvent)
    assert pushed.payload.profile_id == "blue_wave"
    assert svc.current_visitor() is not None  # the local UI cache is unaffected
    # Counted as relayed, not acked: door-api confirmed it, the ESP32 did not.
    snap = svc.metrics_snapshot()
    assert snap["esp32_profile_updates_relayed"] == 1.0
    assert snap["esp32_profile_updates_acked"] == 0.0
    assert svc.health()["esp32_profile_push_status"] == "ok"


def test_same_profile_refresh_is_throttled_on_the_relay_path(ssd_settings: Settings) -> None:
    clock = FakeClock()
    relay = _RecordingProfileRelay()
    svc = _build(ssd_settings, clock, relay)

    emb = _emb()
    svc.core.process_capture(face(emb))
    svc.core.process_capture(face(emb))  # high-priority first push
    assert len(relay.sent) == 1

    # A refresh well inside the throttle window (default 1000 ms) is dropped: the ESP32
    # cache is still warm, so re-POSTing the same profile every frame is wasted traffic.
    clock.advance(300)
    svc.core.process_capture(face(emb))
    assert len(relay.sent) == 1

    # Past the window, the next refresh goes, keeping the cache from lapsing.
    clock.advance(1_000)
    svc.core.process_capture(face(emb))
    assert len(relay.sent) == 2


def test_expiry_relays_a_profile_clear(ssd_settings: Settings) -> None:
    clock = FakeClock()
    relay = _RecordingProfileRelay()
    svc = _build(ssd_settings, clock, relay)

    emb = _emb()
    svc.core.process_capture(face(emb))
    svc.core.process_capture(face(emb))
    assert [e.type for e in relay.sent] == ["door.profile_update"]

    clock.advance(ssd_settings.identity_cache_ttl_ms)
    svc.core.tick()

    assert relay.sent[-1].type == "door.profile_clear"
    assert svc.current_visitor() is None
    assert svc.metrics_snapshot()["esp32_profile_clears_relayed"] == 1.0


# -- the leg must not look alive while doing nothing ------------------------


def test_a_push_door_api_could_not_send_is_a_counted_failure(ssd_settings: Settings) -> None:
    """door-api answers 202 with `status: unavailable` when it has no ESP32 (or `failed`
    when the controller is offline). That used to count as acked and /health stayed
    `ok` — the light leg dead and invisible."""
    clock = FakeClock()
    relay = _RecordingProfileRelay(
        fail_with=ProfileRelayNotSent("door-api esp32 profile relay reported status 'unavailable'")
    )
    svc = _build(ssd_settings, clock, relay)

    emb = _emb()
    svc.core.process_capture(face(emb))
    svc.core.process_capture(face(emb))

    snap = svc.metrics_snapshot()
    assert snap["esp32_profile_send_failures"] == 1.0
    assert snap["esp32_profile_updates_relayed"] == 0.0
    health = svc.health()
    assert health["esp32_profile_push_status"] == "degraded"
    assert health["status"] == "degraded"
    assert "unavailable" in str(health["esp32_profile_warning"])


def test_a_door_with_no_esp32_path_says_so_in_health(ssd_settings: Settings) -> None:
    """No local transport and no relay: every push is dropped. That is a supported
    configuration (mock, CI, no DOOR_API_INTERNAL_EVENT_TOKEN), so it is not a fault —
    but it must be reported rather than answered with `ok`."""
    svc = _build(ssd_settings, FakeClock(), relay=None)

    emb = _emb()
    svc.core.process_capture(face(emb))
    svc.core.process_capture(face(emb))

    health = svc.health()
    assert health["esp32_profile_push_status"] == "disabled"
    assert "no door-api relay" in str(health["esp32_profile_warning"])
    assert health["status"] == "ok"  # reported, not degraded — mirrors event_forwarding


# -- HttpProfileTransport reads door-api's verdict --------------------------


class _FakeResponse(io.BytesIO):
    status = 202

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _transport_with_body(
    monkeypatch: pytest.MonkeyPatch, body: bytes | str
) -> HttpProfileTransport:
    raw = body if isinstance(body, bytes) else body.encode("utf-8")

    def _urlopen(_request: Any, timeout: float) -> _FakeResponse:  # noqa: ARG001
        return _FakeResponse(raw)

    monkeypatch.setattr("door_visiond.event_forwarder.urllib.request.urlopen", _urlopen)
    return HttpProfileTransport(base_url="http://127.0.0.1:8080", token="t")


def _update() -> DoorboardEvent:
    return make_door_profile_update(
        clock=FakeClock(),
        door_id="primary",
        trace_id=uuid4(),
        profile_id="blue_wave",
        expires_at_monotonic_ms=2_500,
        priority="high",
    )


def test_transport_accepts_a_push_door_api_reports_as_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _transport_with_body(monkeypatch, json.dumps({"accepted": True, "status": "sent"}))
    transport.send(_update())  # no raise


@pytest.mark.parametrize("reported", ["unavailable", "failed"])
def test_transport_refuses_a_push_door_api_did_not_put_on_the_wire(
    monkeypatch: pytest.MonkeyPatch, reported: str
) -> None:
    body = json.dumps({"accepted": True, "status": reported})
    transport = _transport_with_body(monkeypatch, body)
    with pytest.raises(ProfileRelayNotSent, match=reported):
        transport.send(_update())


def test_transport_refuses_a_body_it_cannot_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 2xx with no verdict proves nothing about the light."""
    transport = _transport_with_body(monkeypatch, b"not json")
    with pytest.raises(ProfileRelayNotSent):
        transport.send(_update())
