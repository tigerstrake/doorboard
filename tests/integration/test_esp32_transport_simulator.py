from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from doorboard_contracts import EVENT_ADAPTER, DoorboardEvent
from doorboard_event_client import (
    Esp32ProtocolTransport,
    Esp32TransportOptions,
    WireMessage,
    decode_wire_message,
    encode_wire_message,
    monotonic_ms,
    open_socketpair_streams,
    uuid7_now,
)
from doorboard_simulator.clock import SimClock
from doorboard_simulator.esp32 import FakeEsp32Transport
from doorboard_simulator.events import EventFactory


async def _next_event(events: AsyncIterator[DoorboardEvent]) -> DoorboardEvent:
    return await asyncio.wait_for(anext(events), timeout=0.5)


async def _fake_esp32_peer(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    fake: FakeEsp32Transport,
) -> None:
    sender_boot_id = "pi-test"
    try:
        while True:
            raw = await reader.readline()
            if raw == b"":
                return
            msg = decode_wire_message(raw.strip())
            if msg.message_type == "hello":
                raw_boot_id = msg.payload.get("boot_id")
                if isinstance(raw_boot_id, str):
                    sender_boot_id = raw_boot_id
            ack = fake.receive_from_pi(msg, sender_boot_id=sender_boot_id)
            if ack is not None:
                writer.write(encode_wire_message(ack))
                await writer.drain()
    finally:
        writer.close()


async def _send_fake_event(writer: asyncio.StreamWriter, msg: WireMessage) -> None:
    writer.write(encode_wire_message(msg))
    await writer.drain()


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


def test_real_engine_round_trips_profile_update_against_simulator_fake() -> None:
    async def run() -> None:
        pi_reader, pi_writer, fake_reader, fake_writer = await open_socketpair_streams()
        clock = SimClock()
        fake = FakeEsp32Transport(clock, EventFactory(clock))
        peer_task = asyncio.create_task(_fake_esp32_peer(fake_reader, fake_writer, fake))
        transport = Esp32ProtocolTransport.from_streams(
            pi_reader,
            pi_writer,
            options=Esp32TransportOptions(
                pi_boot_id="pi-test",
                sw_version="doorboard-test",
                ack_timeout_ms=10,
                auto_start_tasks=False,
            ),
        )
        await transport.start()
        event = _event(
            "door.profile_update",
            {
                "profile_id": "blue_wave",
                "expires_at_monotonic_ms": monotonic_ms() + 2_500,
                "priority": "normal",
            },
        )

        await transport.send_event(event)

        assert fake.cached_profile_id == "blue_wave"
        assert fake.side_effects.count("profile_update:blue_wave") == 1
        await transport.close()
        peer_task.cancel()
        await asyncio.gather(peer_task, return_exceptions=True)

    asyncio.run(run())


def test_real_engine_retries_without_duplicate_fake_side_effects() -> None:
    async def run() -> None:
        pi_reader, pi_writer, fake_reader, fake_writer = await open_socketpair_streams()
        clock = SimClock()
        fake = FakeEsp32Transport(clock, EventFactory(clock))
        fake.drop_next_acks(1)
        peer_task = asyncio.create_task(_fake_esp32_peer(fake_reader, fake_writer, fake))
        transport = Esp32ProtocolTransport.from_streams(
            pi_reader,
            pi_writer,
            options=Esp32TransportOptions(
                pi_boot_id="pi-test",
                sw_version="doorboard-test",
                ack_timeout_ms=10,
                max_retries=2,
                auto_start_tasks=False,
            ),
        )
        await transport.start()
        msg = transport.make_message(
            "profile_update",
            {"profile_id": "blue_wave", "ttl_ms": 2500, "priority": "normal"},
        )

        await transport.send(msg)

        assert transport.status().tx_retries == 1
        assert fake.side_effects.count("profile_update:blue_wave") == 1
        await transport.close()
        peer_task.cancel()
        await asyncio.gather(peer_task, return_exceptions=True)

    asyncio.run(run())


def test_real_engine_receives_simulator_button_event_exactly_once() -> None:
    async def run() -> None:
        pi_reader, pi_writer, fake_reader, fake_writer = await open_socketpair_streams()
        clock = SimClock()
        fake = FakeEsp32Transport(clock, EventFactory(clock))
        peer_task = asyncio.create_task(_fake_esp32_peer(fake_reader, fake_writer, fake))
        transport = Esp32ProtocolTransport.from_streams(
            pi_reader,
            pi_writer,
            options=Esp32TransportOptions(auto_start_tasks=False),
        )
        await transport.start()
        msg = await fake.emit_button_press()

        await _send_fake_event(fake_writer, msg)
        await _send_fake_event(fake_writer, msg)
        event = await _next_event(transport.events())

        assert event.type == "door.button_pressed"
        assert str(event.payload.press_id) == msg.payload["press_id"]
        assert transport.metrics().duplicate_rx == 1
        await transport.close()
        peer_task.cancel()
        await asyncio.gather(peer_task, return_exceptions=True)

    asyncio.run(run())
