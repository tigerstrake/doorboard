"""In production door-visiond has no ESP32 transport (door-api owns the single UART),
so the proactive profile push is relayed to door-api over loopback instead (ADR-0040).

These pin that the relay actually fires on a stable match — the personalized-light leg
that was dead before, because door-visiond built the push and had nowhere to send it —
and that a same-profile refresh is throttled so it is not a loopback POST per frame.
"""

from __future__ import annotations

from door_visiond.clock import FakeClock
from door_visiond.embedder import MockEmbedder
from door_visiond.enrollment import ProfileSpec
from door_visiond.events import EventEmitter
from door_visiond.service import VisiondService
from door_visiond.settings import Settings
from doorboard_contracts.events import DoorboardEvent

from .conftest import CONSENT_VERSION, TEST_DIM, face


class _CollectingEmitter(EventEmitter):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[DoorboardEvent] = []

    def emit(self, event: DoorboardEvent) -> None:
        self.events.append(event)


class _RecordingProfileRelay:
    """A door-api profile relay double: records the contract events it is handed."""

    def __init__(self) -> None:
        self.sent: list[DoorboardEvent] = []

    def send(self, event: DoorboardEvent) -> None:
        self.sent.append(event)


def _profile(profile_id: str = "blue_wave") -> ProfileSpec:
    return ProfileSpec(profile_id=profile_id, color="#0000ff", sound=None)


def _build(settings: Settings, clock: FakeClock, relay: _RecordingProfileRelay) -> VisiondService:
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
    assert relay.sent[0].payload.profile_id == "blue_wave"
    assert svc.current_visitor() is not None  # the local UI cache is unaffected


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
