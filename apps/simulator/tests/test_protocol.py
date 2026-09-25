from __future__ import annotations

import asyncio

from door_api.adapters import WireMessage
from doorboard_simulator.clock import SimClock
from doorboard_simulator.esp32 import CONSTANT_BOOT_ID, SEEN_RING_ENTRIES, FakeEsp32Transport
from doorboard_simulator.events import EventFactory


def _transport(*, random_boot_id: bool = True) -> tuple[SimClock, FakeEsp32Transport]:
    clock = SimClock()
    return clock, FakeEsp32Transport(clock, EventFactory(clock), random_boot_id=random_boot_id)


def _pi_hello(seq: int, boot_id: str) -> WireMessage:
    return WireMessage(
        v=1,
        seq=seq,
        message_type="hello",
        ack=None,
        payload={"sw_version": "doorboard-test", "proto_v": 1, "boot_id": boot_id},
    )


def _pi_profile(seq: int, profile_id: str = "blue_wave") -> WireMessage:
    return WireMessage(
        v=1,
        seq=seq,
        message_type="profile_update",
        ack=None,
        payload={"profile_id": profile_id, "ttl_ms": 2500, "priority": "normal"},
    )


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


def test_dedupe_ring_forgets_after_sixteen_frames_like_the_firmware() -> None:
    _, esp32 = _transport()
    for seq in range(1, SEEN_RING_ENTRIES + 1):
        assert esp32.receive_from_pi(_pi_profile(seq), sender_boot_id="pi-boot") is not None
    assert esp32.seen_pi_messages == SEEN_RING_ENTRIES

    # Still remembered: the ring is exactly full.
    esp32.receive_from_pi(_pi_profile(1), sender_boot_id="pi-boot")
    assert esp32.side_effects.count("profile_update:blue_wave") == SEEN_RING_ENTRIES

    # One more distinct frame evicts seq 1, so its replay is applied again.
    esp32.receive_from_pi(_pi_profile(SEEN_RING_ENTRIES + 1), sender_boot_id="pi-boot")
    esp32.receive_from_pi(_pi_profile(1), sender_boot_id="pi-boot")
    assert esp32.side_effects.count("profile_update:blue_wave") == SEEN_RING_ENTRIES + 2
    assert esp32.seen_pi_messages == SEEN_RING_ENTRIES


def test_an_ack_consumes_a_sequence_number() -> None:
    _, esp32 = _transport()
    msg = esp32.make_message("profile_clear", {"reason": "test"})
    ack = esp32.receive_from_pi(msg, sender_boot_id="pi-boot")
    assert ack is not None
    assert ack.seq == msg.seq + 1
    assert esp32.make_message("heartbeat", {}).seq == msg.seq + 2

    # A lost ack (drop_next_acks) was still built on the device: its seq is gone.
    esp32.drop_next_acks(1)
    lost = esp32.make_message("profile_clear", {"reason": "again"})
    assert esp32.receive_from_pi(lost, sender_boot_id="pi-boot") is None
    assert esp32.make_message("heartbeat", {}).seq == lost.seq + 2


def test_pi_hello_is_answered_with_a_hello_and_a_new_pi_boot_forgets_the_ring() -> None:
    _, esp32 = _transport()
    assert esp32.receive_from_pi(_pi_hello(1, "pi-a"), sender_boot_id="pi-a") is not None
    answered = esp32.take_outbound()
    assert [frame.message_type for frame in answered] == ["hello"]
    assert answered[0].payload["boot_id"] == esp32.boot_id
    assert esp32.take_outbound() == []

    esp32.receive_from_pi(_pi_profile(2), sender_boot_id="pi-a")
    # A retransmitted hello is a duplicate: no second handshake.
    esp32.receive_from_pi(_pi_hello(1, "pi-a"), sender_boot_id="pi-a")
    assert esp32.take_outbound() == []

    # The Pi restarted: its seqs start over and must not be deduped.
    esp32.receive_from_pi(_pi_hello(1, "pi-b"), sender_boot_id="pi-b")
    assert [frame.message_type for frame in esp32.take_outbound()] == ["hello"]
    esp32.receive_from_pi(_pi_profile(2), sender_boot_id="pi-b")
    assert esp32.side_effects.count("profile_update:blue_wave") == 2


def test_fallback_edge_plays_the_animation_once_and_re_hellos() -> None:
    clock, esp32 = _transport()
    # Boot state is fallback but not an edge.
    assert esp32.fallback_active is True
    assert "fallback_animation" not in esp32.side_effects
    assert esp32.take_outbound() == []

    esp32.receive_from_pi(_pi_hello(1, "pi-a"), sender_boot_id="pi-a")
    esp32.take_outbound()
    clock.advance_to(5001)
    assert esp32.fallback_active is True
    assert esp32.side_effects.count("fallback_animation") == 1
    assert [frame.message_type for frame in esp32.take_outbound()] == ["hello"]

    clock.advance_to(6001)
    assert esp32.fallback_active is True
    assert esp32.side_effects.count("fallback_animation") == 1
    assert esp32.take_outbound() == []

    # While in fallback a press shows the fallback feedback after the generic one.
    asyncio.run(esp32.emit_button_press())
    assert esp32.side_effects[-2:] == ("generic_feedback", "fallback_feedback")


def test_boot_id_is_per_boot_unless_pinned_constant() -> None:
    _, random_a = _transport()
    _, random_b = _transport()
    assert random_a.boot_id != random_b.boot_id
    before = random_a.boot_id
    random_a.reboot()
    assert random_a.boot_id != before
    assert [frame.message_type for frame in random_a.take_outbound()] == ["hello"]
    assert random_a.side_effects[-1] == "boot"

    _, constant = _transport(random_boot_id=False)
    assert constant.boot_id == CONSTANT_BOOT_ID
    constant.reboot()
    assert constant.boot_id == CONSTANT_BOOT_ID
    assert constant.take_outbound()[0].seq == 1


def test_heartbeat_carries_the_controllers_counters() -> None:
    _, esp32 = _transport()
    bad = WireMessage(v=99, seq=1, message_type="hello", ack=None, payload={})
    esp32.receive_from_pi(bad, sender_boot_id="pi-boot")
    msg = asyncio.run(esp32.heartbeat_from_esp32())
    assert msg.payload["rx_errors"] == 1
    assert msg.payload["tx_retries"] == 0
    assert msg.payload["tx_dropped"] == 0
