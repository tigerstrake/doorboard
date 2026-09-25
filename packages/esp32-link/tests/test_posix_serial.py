"""The production byte transport, exercised through a pseudo-terminal.

`PosixSerialByteTransport` is the code between every bell press and door-api,
and until now it ran only on the door. A pty is not a CH343 bridge, but it is a
real tty: the termios setup, the non-blocking reader, the device-gone path the
supervisor keys its reopen on, and the writer wake-up all go through the kernel
the same way.
"""

from __future__ import annotations

import asyncio
import os
import pty
import time

from doorboard_esp32_link import (
    AckTimeoutError,
    Esp32ProtocolTransport,
    Esp32TransportOptions,
    PosixSerialByteTransport,
)

FRAME = b'{"v":1,"seq":1,"t":"heartbeat","ack":null,"p":{"uptime_s":1,"fallback_active":false}}\n'


async def _open_pty_transport() -> tuple[int, PosixSerialByteTransport]:
    master, slave = pty.openpty()
    transport = await PosixSerialByteTransport.open(os.ttyname(slave), baud_rate=115200)
    os.close(slave)  # the transport holds its own descriptor
    return master, transport


def test_opens_the_slave_by_name_and_reads_what_the_master_writes() -> None:
    async def run() -> None:
        master, transport = await _open_pty_transport()
        try:
            os.write(master, FRAME)
            received = b""
            while not received.endswith(b"\n"):
                received += await asyncio.wait_for(transport.read(), timeout=1.0)
            assert received == FRAME

            await asyncio.wait_for(transport.write(b"to-the-controller\n"), timeout=1.0)
            assert os.read(master, 64) == b"to-the-controller\n"
        finally:
            os.close(master)
            await transport.close()

    asyncio.run(run())


def test_master_going_away_ends_the_read_stream_with_an_empty_chunk() -> None:
    # This is the "USB bridge unplugged" path: the supervisor reopens only when
    # the read loop sees b"", so a regression here would present as a bell that
    # stays dead after a USB hiccup.
    async def run() -> None:
        master, transport = await _open_pty_transport()
        os.close(master)
        try:
            assert await asyncio.wait_for(transport.read(), timeout=1.0) == b""
        finally:
            await transport.close()

    asyncio.run(run())


def test_a_write_that_cannot_drain_honours_the_deadline_and_releases_the_writer() -> None:
    async def run() -> None:
        master, transport = await _open_pty_transport()
        try:
            started = time.monotonic()
            try:
                # Nobody reads the master, so the slave's output buffer fills and
                # os.write raises BlockingIOError; the transport must then sleep
                # on the loop's writer callback, not spin, and give up on time.
                await asyncio.wait_for(transport.write(b"x" * 1_000_000), timeout=0.2)
            except TimeoutError:
                pass
            else:  # pragma: no cover
                raise AssertionError("a write nobody drains must not complete")
            assert time.monotonic() - started < 1.0

            # The writer callback was removed on cancellation: closing is clean
            # and a fresh write after the master drains goes through.
            os.read(master, 1_000_000)
            await asyncio.wait_for(transport.write(b"after\n"), timeout=1.0)
            assert os.read(master, 64).endswith(b"after\n")
        finally:
            os.close(master)
            await transport.close()

    asyncio.run(run())


def test_protocol_send_over_a_stalled_tty_raises_within_the_ack_timeout() -> None:
    async def run() -> None:
        master, byte_transport = await _open_pty_transport()
        transport = Esp32ProtocolTransport(
            byte_transport,
            options=Esp32TransportOptions(ack_timeout_ms=50, max_retries=0, auto_start_tasks=False),
        )
        await transport.start()
        try:
            # Wedge the tty first, then ask for an acked send.
            with_deadline = asyncio.create_task(byte_transport.write(b"x" * 1_000_000))
            await asyncio.sleep(0.05)
            msg = transport.make_message("effect_play", {"effect_id": "sparkle", "duration_ms": 1})
            started = time.monotonic()
            try:
                await transport.send(msg)
            except AckTimeoutError:
                pass
            else:  # pragma: no cover
                raise AssertionError("a send over a wedged tty must not resolve")
            assert time.monotonic() - started < 1.0
            assert transport.metrics().tx_write_timeouts == 1
            with_deadline.cancel()
            await asyncio.gather(with_deadline, return_exceptions=True)
        finally:
            os.close(master)
            await transport.close()

    asyncio.run(run())
