from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from door_visiond.clock import FakeClock
from door_visiond.embedder import MockEmbedder
from door_visiond.enrollment import ProfileSpec
from door_visiond.events import EventEmitter
from door_visiond.service import VisiondService
from door_visiond.settings import Settings
from doorboard_contracts.events import DoorboardEvent
from doorboard_esp32_link import Esp32TransportStatus, WireMessage
from doorboard_simulator.clock import SimClock
from doorboard_simulator.esp32 import FakeEsp32Transport
from doorboard_simulator.events import EventFactory

from .conftest import TEST_DIM, face


class _CollectingEmitter(EventEmitter):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[DoorboardEvent] = []

    def emit(self, event: DoorboardEvent) -> None:
        self.events.append(event)


class _FailingTransport:
    def __init__(self) -> None:
        self.messages: list[WireMessage] = []

    async def send(self, msg: WireMessage) -> WireMessage:
        self.messages.append(msg)
        raise TimeoutError("offline")

    async def events(self) -> AsyncIterator[DoorboardEvent]:
        # Never yields; this transport exists only to exercise the offline send path.
        empty: list[DoorboardEvent] = []
        for event in empty:
            yield event

    def status(self) -> Esp32TransportStatus:
        return Esp32TransportStatus(
            connected=False,
            last_heartbeat_mono_ms=None,
            rx_errors=0,
            tx_retries=0,
        )


def _profile(profile_id: str = "blue_wave") -> ProfileSpec:
    return ProfileSpec(profile_id=profile_id, color="#0000ff", sound=None)


def _enroll_and_stabilize(
    *,
    settings: Settings,
    clock: FakeClock,
    esp32: FakeEsp32Transport | _FailingTransport,
    emitter: _CollectingEmitter,
    seed: bytes = b"alex-photo-bytes",
) -> tuple[VisiondService, str]:
    svc = VisiondService(settings, clock=clock, emitter=emitter, esp32_transport=esp32)
    svc.startup()
    result = svc.enroll(
        display_name="Alex",
        consent_version="v1",
        consent_confirmed=True,
        images=[seed],
        profile=_profile(),
    )
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(seed)
    svc.core.process_capture(face(emb))
    svc.core.process_capture(face(emb))
    return svc, result.person_id


def test_cache_refresh_pushes_profile_update_with_wire_ttl(ssd_settings: Settings) -> None:
    clock = FakeClock()
    sim_clock = SimClock()
    esp32 = FakeEsp32Transport(sim_clock, EventFactory(sim_clock))
    emitter = _CollectingEmitter()
    svc, _person_id = _enroll_and_stabilize(
        settings=ssd_settings,
        clock=clock,
        esp32=esp32,
        emitter=emitter,
    )

    assert esp32.cached_profile_id == "blue_wave"
    assert esp32.side_effects[-1] == "profile_update:blue_wave"
    assert svc.metrics_snapshot()["esp32_profile_updates_acked"] == 1.0

    clock.advance(1_000)
    sim_clock.advance_by(1_000)
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(b"alex-photo-bytes")
    svc.core.process_capture(face(emb))

    assert esp32.cached_profile_id == "blue_wave"
    assert esp32.side_effects[-1] == "profile_update:blue_wave"
    assert svc.metrics_snapshot()["esp32_profile_updates_acked"] == 2.0


def test_expiry_emits_identity_expired_and_profile_clear(ssd_settings: Settings) -> None:
    clock = FakeClock()
    sim_clock = SimClock()
    esp32 = FakeEsp32Transport(sim_clock, EventFactory(sim_clock))
    emitter = _CollectingEmitter()
    svc, person_id = _enroll_and_stabilize(
        settings=ssd_settings,
        clock=clock,
        esp32=esp32,
        emitter=emitter,
    )

    clock.advance(ssd_settings.identity_cache_ttl_ms)
    sim_clock.advance_by(ssd_settings.identity_cache_ttl_ms)
    svc.core.tick()

    assert svc.current_visitor() is None
    assert esp32.cached_profile_id is None
    assert esp32.side_effects[-1] == "profile_clear:expired"
    assert [e.type for e in emitter.events].count("vision.identity_expired") == 1
    expired = next(e for e in emitter.events if e.type == "vision.identity_expired")
    assert expired.payload.person_id == person_id
    assert svc.metrics_snapshot()["esp32_profile_clears_acked"] == 1.0


def test_unenroll_propagates_admin_clear_and_heartbeat_null(ssd_settings: Settings) -> None:
    clock = FakeClock()
    sim_clock = SimClock()
    events = EventFactory(sim_clock)
    esp32 = FakeEsp32Transport(sim_clock, events)
    emitter = _CollectingEmitter()
    svc, person_id = _enroll_and_stabilize(
        settings=ssd_settings,
        clock=clock,
        esp32=esp32,
        emitter=emitter,
    )

    out = svc.unenroll(person_id)
    heartbeat = asyncio.run(esp32.heartbeat_from_esp32())
    health_event = esp32.to_contract_event(heartbeat)

    assert out == {"deleted": True, "archive_purge": "queued"}
    assert esp32.side_effects[-1] == "profile_clear:admin"
    assert health_event.type == "door.controller_health"
    assert health_event.payload.cached_profile_id is None
    assert svc.current_visitor() is None


def test_privacy_mode_flip_flushes_cache_and_keeps_door_cache_miss(
    ssd_settings: Settings,
) -> None:
    clock = FakeClock()
    sim_clock = SimClock()
    esp32 = FakeEsp32Transport(sim_clock, EventFactory(sim_clock))
    emitter = _CollectingEmitter()
    svc, _person_id = _enroll_and_stabilize(
        settings=ssd_settings,
        clock=clock,
        esp32=esp32,
        emitter=emitter,
    )

    svc.set_privacy_mode(enabled=True, changed_by="admin")

    assert svc.privacy_enabled is True
    assert svc.current_visitor() is None
    assert esp32.cached_profile_id is None
    assert esp32.side_effects[-1] == "profile_clear:privacy_mode"


def test_esp32_offline_preserves_ui_cache_and_surfaces_warning(
    ssd_settings: Settings,
) -> None:
    clock = FakeClock()
    esp32 = _FailingTransport()
    emitter = _CollectingEmitter()
    svc, _person_id = _enroll_and_stabilize(
        settings=ssd_settings,
        clock=clock,
        esp32=esp32,
        emitter=emitter,
    )

    assert svc.current_visitor() is not None
    health = svc.health()
    assert health["status"] == "degraded"
    assert health["esp32_profile_push_status"] == "degraded"
    assert health["esp32_profile_warning"] == "profile push failed: TimeoutError"
    assert svc.metrics_snapshot()["esp32_profile_send_failures"] == 1.0
