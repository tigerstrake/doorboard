from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

from doorboard_contracts import EVENT_ADAPTER, DoorboardEvent
from doorboard_esp32_link import (
    MAX_FRAME_BYTES,
    AckTimeoutError,
    Esp32ProtocolTransport,
    Esp32TransportOptions,
    WireMessage,
    decode_wire_message,
    encode_wire_message,
    uuid7_now,
)


class ManualClock:
    def __init__(self) -> None:
        self.mono_ms = 1_000

    def now(self) -> int:
        return self.mono_ms

    def advance(self, ms: int) -> None:
        self.mono_ms += ms


class QueueByteTransport:
    def __init__(self) -> None:
        self.inbound: asyncio.Queue[bytes] = asyncio.Queue()
        self.writes: asyncio.Queue[bytes] = asyncio.Queue()

    async def read(self) -> bytes:
        return await self.inbound.get()

    async def write(self, data: bytes) -> None:
        await self.writes.put(data)

    async def close(self) -> None:
        await self.inbound.put(b"")

    async def feed(self, msg: WireMessage) -> None:
        await self.inbound.put(encode_wire_message(msg))


class FailingReadTransport:
    async def read(self) -> bytes:
        raise OSError("serial device disappeared")

    async def write(self, data: bytes) -> None:
        raise AssertionError(f"unexpected write: {data!r}")

    async def close(self) -> None:
        pass


def _options(*, max_retries: int = 2) -> Esp32TransportOptions:
    return Esp32TransportOptions(
        pi_boot_id="pi-test",
        sw_version="doorboard-test",
        ack_timeout_ms=5,
        max_retries=max_retries,
        heartbeat_timeout_ms=25,
        monitor_interval_ms=5,
        auto_start_tasks=False,
    )


async def _next_event(events: AsyncIterator[DoorboardEvent]) -> DoorboardEvent:
    return await asyncio.wait_for(anext(events), timeout=0.5)


def _event(event_type: str, payload: dict[str, object]) -> DoorboardEvent:
    return EVENT_ADAPTER.validate_python(
        {
            "event_id": uuid7_now(),
            "type": event_type,
            "source": "test",
            "occurred_at": datetime.now(UTC),
            "monotonic_ms": 1_000,
            "door_id": "primary",
            "trace_id": uuid7_now(),
            "payload": payload,
        }
    )


def test_decode_rejects_malformed_and_oversize_frames() -> None:
    async def run() -> None:
        clock = ManualClock()
        bytes_ = QueueByteTransport()
        transport = Esp32ProtocolTransport(bytes_, options=_options(), now_mono_ms=clock.now)
        await transport.start()
        await bytes_.inbound.put(b"{not-json}\n")
        await bytes_.inbound.put(b"x" * (MAX_FRAME_BYTES + 1) + b"\n")
        await bytes_.feed(
            WireMessage(
                v=1,
                seq=1,
                message_type="heartbeat",
                ack=None,
                payload={"uptime_s": 1, "fallback_active": False, "cached_profile_id": None},
            )
        )

        event = await _next_event(transport.events())

        assert event.type == "door.controller_health"
        assert transport.status().rx_errors == 2
        assert transport.health_check().status == "degraded"
        await transport.close()

    asyncio.run(run())


def test_malformed_payload_is_counted_and_read_loop_continues() -> None:
    async def run() -> None:
        bytes_ = QueueByteTransport()
        transport = Esp32ProtocolTransport(bytes_, options=_options())
        await transport.start()
        events = transport.events()
        await bytes_.feed(
            WireMessage(
                v=1,
                seq=1,
                message_type="hello",
                ack=None,
                payload={"fw_version": "sim", "proto_v": 1, "boot_id": "esp-boot-1"},
            )
        )
        await bytes_.feed(
            WireMessage(
                v=1,
                seq=2,
                message_type="button_event",
                ack=None,
                payload={
                    "press_id": "c0a8b1d2-0f0e-4a6b-b111-5a4e2f9b7788",
                    "pressed_at_mono_ms": 123,
                    "profile_id": None,
                },
            )
        )
        good = WireMessage(
            v=1,
            seq=3,
            message_type="button_event",
            ack=None,
            payload={
                "press_id": "c0a8b1d2-0f0e-4a6b-b111-5a4e2f9b7789",
                "pressed_at_mono_ms": 124,
                "had_cached_profile": False,
                "profile_id": None,
            },
        )
        await bytes_.feed(good)

        event = await _next_event(events)

        assert event.type == "door.button_pressed"
        assert str(event.payload.press_id) == good.payload["press_id"]
        assert transport.status().rx_errors == 1
        assert transport.health_check().status == "degraded"
        await transport.close()

    asyncio.run(run())


def test_read_oserror_marks_link_down_without_unhandled_task_failure() -> None:
    async def run() -> None:
        transport = Esp32ProtocolTransport(FailingReadTransport(), options=_options())
        await transport.start()
        await asyncio.sleep(0)

        assert transport.status().connected is False
        assert transport.health_check().status == "degraded"
        await transport.close()

    asyncio.run(run())


def test_send_retries_until_ack_arrives() -> None:
    async def run() -> None:
        bytes_ = QueueByteTransport()
        transport = Esp32ProtocolTransport(bytes_, options=_options())
        await transport.start()
        msg = transport.make_message(
            "profile_update",
            {"profile_id": "blue_wave", "ttl_ms": 2500, "priority": "normal"},
        )
        send_task = asyncio.create_task(transport.send(msg))

        first_write = await asyncio.wait_for(bytes_.writes.get(), timeout=0.5)
        assert decode_wire_message(first_write.strip()).seq == msg.seq
        second_write = await asyncio.wait_for(bytes_.writes.get(), timeout=0.5)
        assert decode_wire_message(second_write.strip()).seq == msg.seq
        await bytes_.feed(WireMessage(v=1, seq=100, message_type="ack", ack=msg.seq, payload={}))
        ack = await asyncio.wait_for(send_task, timeout=0.5)

        assert ack.ack == msg.seq
        assert transport.status().tx_retries == 1
        await transport.close()

    asyncio.run(run())


def test_send_timeout_surfaces_offline_without_faking_success() -> None:
    async def run() -> None:
        bytes_ = QueueByteTransport()
        transport = Esp32ProtocolTransport(bytes_, options=_options(max_retries=1))
        await transport.start()
        msg = transport.make_message(
            "effect_play",
            {"effect_id": "generic_chime", "duration_ms": 1000},
        )

        try:
            await transport.send(msg)
        except AckTimeoutError:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected ack timeout")

        assert transport.metrics().tx_timeouts == 1
        assert transport.health_check().status == "down"
        await transport.close()

    asyncio.run(run())


def test_auto_start_surfaces_missing_hello_ack_as_down_without_crashing() -> None:
    async def run() -> None:
        bytes_ = QueueByteTransport()
        transport = Esp32ProtocolTransport(
            bytes_,
            options=Esp32TransportOptions(
                pi_boot_id="pi-test",
                sw_version="doorboard-test",
                ack_timeout_ms=5,
                max_retries=0,
                heartbeat_interval_ms=1_000,
                auto_start_tasks=True,
            ),
        )

        await transport.start()

        assert transport.status().connected is False
        assert transport.metrics().tx_timeouts == 1
        assert transport.health_check().status == "down"
        await transport.close()

    asyncio.run(run())


def test_duplicate_inbound_event_is_acked_but_translated_once() -> None:
    async def run() -> None:
        bytes_ = QueueByteTransport()
        transport = Esp32ProtocolTransport(bytes_, options=_options())
        await transport.start()
        await bytes_.feed(
            WireMessage(
                v=1,
                seq=1,
                message_type="hello",
                ack=None,
                payload={"fw_version": "sim", "proto_v": 1, "boot_id": "esp-boot-1"},
            )
        )
        button = WireMessage(
            v=1,
            seq=2,
            message_type="button_event",
            ack=None,
            payload={
                "press_id": "c0a8b1d2-0f0e-4a6b-b111-5a4e2f9b7788",
                "pressed_at_mono_ms": 123,
                "had_cached_profile": True,
                "profile_id": "blue_wave",
            },
        )
        await bytes_.feed(button)
        await bytes_.feed(button)

        event = await _next_event(transport.events())
        first_ack = decode_wire_message((await bytes_.writes.get()).strip())
        second_ack = decode_wire_message((await bytes_.writes.get()).strip())
        third_ack = decode_wire_message((await bytes_.writes.get()).strip())

        assert event.type == "door.button_pressed"
        assert event.payload.profile_id == "blue_wave"
        assert first_ack.message_type == "ack"
        assert second_ack.ack == button.seq
        assert third_ack.ack == button.seq
        assert transport.metrics().duplicate_rx == 1
        await transport.close()

    asyncio.run(run())


def test_inbound_dedupe_state_stays_bounded() -> None:
    async def run() -> None:
        bytes_ = QueueByteTransport()
        transport = Esp32ProtocolTransport(
            bytes_,
            options=Esp32TransportOptions(
                pi_boot_id="pi-test",
                sw_version="doorboard-test",
                ack_timeout_ms=5,
                max_retries=2,
                heartbeat_timeout_ms=25,
                monitor_interval_ms=5,
                auto_start_tasks=False,
                dedupe_recent_window=8,
            ),
        )
        await transport.start()
        await bytes_.feed(
            WireMessage(
                v=1,
                seq=1,
                message_type="hello",
                ack=None,
                payload={"fw_version": "sim", "proto_v": 1, "boot_id": "esp-boot-1"},
            )
        )
        for seq in range(2, 102):
            await bytes_.feed(
                WireMessage(
                    v=1,
                    seq=seq,
                    message_type="heartbeat",
                    ack=None,
                    payload={
                        "uptime_s": seq,
                        "fallback_active": False,
                        "cached_profile_id": None,
                    },
                )
            )

        await _next_event(transport.events())
        for _ in range(99):
            await _next_event(transport.events())

        assert transport.inbound_dedupe_entries <= 8
        await transport.close()

    asyncio.run(run())


def test_boot_id_change_allows_same_sequence_after_reboot() -> None:
    async def run() -> None:
        bytes_ = QueueByteTransport()
        transport = Esp32ProtocolTransport(bytes_, options=_options())
        await transport.start()
        events = transport.events()

        for boot_id, profile_id in (("esp-boot-1", "first"), ("esp-boot-2", "second")):
            await bytes_.feed(
                WireMessage(
                    v=1,
                    seq=1,
                    message_type="hello",
                    ack=None,
                    payload={"fw_version": "sim", "proto_v": 1, "boot_id": boot_id},
                )
            )
            await bytes_.feed(
                WireMessage(
                    v=1,
                    seq=2,
                    message_type="button_event",
                    ack=None,
                    payload={
                        "press_id": "c0a8b1d2-0f0e-4a6b-b111-5a4e2f9b7788",
                        "pressed_at_mono_ms": 123,
                        "had_cached_profile": True,
                        "profile_id": profile_id,
                    },
                )
            )

        first = await _next_event(events)
        second = await _next_event(events)

        first_payload = cast(dict[str, object], first.model_dump(mode="python")["payload"])
        second_payload = cast(dict[str, object], second.model_dump(mode="python")["payload"])
        assert first_payload["profile_id"] == "first"
        assert second_payload["profile_id"] == "second"
        assert transport.metrics().duplicate_rx == 0
        await transport.close()

    asyncio.run(run())


def test_heartbeat_loss_emits_link_state_and_health_down() -> None:
    async def run() -> None:
        clock = ManualClock()
        bytes_ = QueueByteTransport()
        transport = Esp32ProtocolTransport(bytes_, options=_options(), now_mono_ms=clock.now)
        await transport.start()
        states = transport.link_state_events()
        await bytes_.feed(
            WireMessage(
                v=1,
                seq=1,
                message_type="heartbeat",
                ack=None,
                payload={"uptime_s": 1, "fallback_active": False, "cached_profile_id": None},
            )
        )
        assert (await asyncio.wait_for(anext(states), timeout=0.5)).connected is True

        clock.advance(26)
        status = transport.status()

        assert status.connected is False
        assert (await asyncio.wait_for(anext(states), timeout=0.5)).reason == "heartbeat timeout"
        assert transport.health_check().status == "down"
        await transport.close()

    asyncio.run(run())


def test_send_event_translates_profile_update_expiry_to_wire_ttl() -> None:
    async def run() -> None:
        clock = ManualClock()
        bytes_ = QueueByteTransport()
        transport = Esp32ProtocolTransport(bytes_, options=_options(), now_mono_ms=clock.now)
        await transport.start()
        event = _event(
            "door.profile_update",
            {
                "profile_id": "blue_wave",
                "expires_at_monotonic_ms": clock.now() + 2500,
                "priority": "normal",
            },
        )
        send_task = asyncio.create_task(transport.send_event(event))
        written = decode_wire_message((await bytes_.writes.get()).strip())
        await bytes_.feed(
            WireMessage(v=1, seq=100, message_type="ack", ack=written.seq, payload={})
        )
        await send_task

        assert written.message_type == "profile_update"
        assert written.payload["ttl_ms"] == 2500
        await transport.close()

    asyncio.run(run())
