from __future__ import annotations

import asyncio

from door_api.adapters import WireMessage
from doorboard_simulator.clock import SimClock
from doorboard_simulator.esp32 import FakeEsp32Transport
from doorboard_simulator.events import EventFactory


def _transport() -> tuple[SimClock, FakeEsp32Transport]:
    clock = SimClock()
    return clock, FakeEsp32Transport(clock, EventFactory(clock))


def test_profile_update_ack_and_duplicate_dedupe() -> None:
    _, esp32 = _transport()
    msg = esp32.make_message(
        "profile_update",
        {"profile_id": "blue_wave", "ttl_ms": 2500, "priority": "normal"},
    )

    ack = esp32.receive_from_pi(msg, sender_boot_id="pi-boot")
    duplicate_ack = esp32.receive_from_pi(msg, sender_boot_id="pi-boot")

    assert ack is not None
    assert ack.message_type == "ack"
    assert ack.ack == msg.seq
    assert duplicate_ack is not None
    assert duplicate_ack.ack == msg.seq
    assert esp32.side_effects.count("profile_update:blue_wave") == 1


def test_retransmit_retries_without_reapplying_side_effects() -> None:
    _, esp32 = _transport()
    esp32.drop_next_acks(2)
    msg = esp32.make_message(
        "profile_update",
        {"profile_id": "blue_wave", "ttl_ms": 2500, "priority": "normal"},
    )

    ack = asyncio.run(esp32.send(msg))

    assert ack.ack == msg.seq
    assert esp32.status().tx_retries == 2
    assert esp32.side_effects.count("profile_update:blue_wave") == 1


def test_profile_cache_ttl_expiry_uses_local_monotonic_time() -> None:
    clock, esp32 = _transport()
    asyncio.run(
        esp32.send(
            esp32.make_message(
                "profile_update",
                {"profile_id": "blue_wave", "ttl_ms": 1000, "priority": "normal"},
            )
        )
    )

    clock.advance_to(999)
    assert esp32.cached_profile_id == "blue_wave"

    clock.advance_to(1000)
    assert esp32.cached_profile_id is None


def test_fallback_starts_after_pi_heartbeat_loss() -> None:
    clock, esp32 = _transport()
    asyncio.run(
        esp32.send(
            esp32.make_message(
                "hello",
                {"sw_version": "doorboard-test", "proto_v": 1, "boot_id": "pi-test"},
            )
        )
    )

    clock.advance_to(5000)
    assert esp32.fallback_active is False

    clock.advance_to(5001)
    assert esp32.fallback_active is True


def test_button_event_reports_cached_profile() -> None:
    _, esp32 = _transport()
    asyncio.run(
        esp32.send(
            esp32.make_message(
                "profile_update",
                {"profile_id": "blue_wave", "ttl_ms": 2500, "priority": "normal"},
            )
        )
    )

    msg = asyncio.run(esp32.emit_button_press())

    assert msg.message_type == "button_event"
    assert msg.payload["had_cached_profile"] is True
    assert msg.payload["profile_id"] == "blue_wave"
    assert "generic_feedback" in esp32.side_effects
    assert "personalized_feedback:blue_wave" in esp32.side_effects


def test_malformed_wire_message_counts_rx_error() -> None:
    _, esp32 = _transport()
    bad = WireMessage(v=99, seq=1, message_type="hello", ack=None, payload={})

    assert esp32.receive_from_pi(bad, sender_boot_id="pi-boot") is None
    assert esp32.status().rx_errors == 1
