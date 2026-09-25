from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from doorboard_contracts import EVENT_ADAPTER, DoorboardEvent
from doorboard_esp32_link import (
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
            frames = ([ack] if ack is not None else []) + fake.take_outbound()
            for frame in frames:
                writer.write(encode_wire_message(frame))
            if frames:
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


def test_pi_recovers_when_the_controller_reboots_with_a_constant_boot_id() -> None:
    # The firmware used to announce the same boot_id on every boot. The Pi's
    # (boot_id, seq) dedupe then took the post-reboot hello (seq 1), heartbeats
    # and button events for stale retransmits of the previous run and acked and
    # dropped them until seq climbed back past the old high-water mark -- the
    # bell was dead for as long as the controller had previously been up. The
    # constant-boot_id knob reproduces that firmware; the Pi now treats a
    # non-retransmit hello as a link restart and recovers on the spot.
    async def run() -> None:
        pi_reader, pi_writer, fake_reader, fake_writer = await open_socketpair_streams()
        clock = SimClock()
        fake = FakeEsp32Transport(clock, EventFactory(clock), random_boot_id=False)
        peer_task = asyncio.create_task(_fake_esp32_peer(fake_reader, fake_writer, fake))
        transport = Esp32ProtocolTransport.from_streams(
            pi_reader,
            pi_writer,
            options=Esp32TransportOptions(
                auto_start_tasks=False, dedupe_recent_window=8, event_queue_size=1024
            ),
        )
        await transport.start()
        events = transport.events()

        # First boot: a hello and a run long enough to push the Pi's high-water
        # mark well past the dedupe window.
        await _send_fake_event(fake_writer, fake.make_hello())
        for _ in range(20):
            await _send_fake_event(fake_writer, await fake.heartbeat_from_esp32())
        first_boot_id = fake.boot_id

        fake.reboot()
        assert fake.boot_id == first_boot_id
        for frame in fake.take_outbound():  # the boot hello, seq 1 again
            await _send_fake_event(fake_writer, frame)
        press = await fake.emit_button_press()
        assert press.seq == 2
        await _send_fake_event(fake_writer, press)

        event = await _next_event(events)
        while event.type != "door.button_pressed":
            event = await _next_event(events)

        assert str(event.payload.press_id) == press.payload["press_id"]
        assert transport.metrics().duplicate_rx == 0
        assert transport.status().connected is True
        await transport.close()
        peer_task.cancel()
        await asyncio.gather(peer_task, return_exceptions=True)

    asyncio.run(run())


def test_pi_hello_is_answered_by_the_fake_and_learns_its_firmware() -> None:
    async def run() -> None:
        pi_reader, pi_writer, fake_reader, fake_writer = await open_socketpair_streams()
        clock = SimClock()
        fake = FakeEsp32Transport(clock, EventFactory(clock))
        peer_task = asyncio.create_task(_fake_esp32_peer(fake_reader, fake_writer, fake))
        transport = Esp32ProtocolTransport.from_streams(
            pi_reader,
            pi_writer,
            options=Esp32TransportOptions(
                pi_boot_id="pi-test", sw_version="doorboard-test", ack_timeout_ms=50
            ),
        )

        await transport.start()  # sends the Pi's hello and awaits its ack
        await asyncio.sleep(0.05)  # the fake's answering hello is right behind the ack

        assert "hello" in fake.side_effects
        assert transport.status().connected is True
        assert transport.metrics().duplicate_rx == 0
        await transport.close()
        peer_task.cancel()
        await asyncio.gather(peer_task, return_exceptions=True)

    asyncio.run(run())
