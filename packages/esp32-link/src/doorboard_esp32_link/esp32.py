from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import socket
import termios
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, Self, cast
from uuid import UUID

from doorboard_contracts import EVENT_ADAPTER, DoorboardEvent, HealthPayload, HealthStatus
from pydantic import ValidationError

PROTO_V = 1
MAX_FRAME_BYTES = 512
DEFAULT_DEDUPE_RECENT_WINDOW = 256
ACK_REQUIRED = {
    "hello",
    "profile_update",
    "profile_clear",
    "effect_play",
    "button_event",
    "knock_event",
    "contact_event",
}
INBOUND_EVENT_TYPES = {
    "heartbeat",
    "button_event",
    "knock_event",
    "contact_event",
}


@dataclass(frozen=True)
class WireMessage:
    v: int
    seq: int
    message_type: str
    ack: int | None
    payload: Mapping[str, object]

    def to_wire_dict(self) -> dict[str, object]:
        return {
            "v": self.v,
            "seq": self.seq,
            "t": self.message_type,
            "ack": self.ack,
            "p": dict(self.payload),
        }


@dataclass(frozen=True)
class Esp32TransportStatus:
    connected: bool
    last_heartbeat_mono_ms: int | None
    rx_errors: int
    tx_retries: int


@dataclass(frozen=True)
class Esp32LinkState:
    connected: bool
    changed_at_mono_ms: int
    reason: str


@dataclass(frozen=True)
class Esp32TransportMetrics:
    connected: int
    last_heartbeat_mono_ms: int
    rx_errors: int
    tx_retries: int
    tx_timeouts: int
    duplicate_rx: int


class Esp32Transport(Protocol):
    async def send(self, msg: WireMessage) -> WireMessage:
        """Send a wire message and resolve with its ack or raise on timeout."""
        ...

    def events(self) -> AsyncIterator[DoorboardEvent]:
        """Yield inbound ESP32 messages translated to contract events exactly once."""
        ...

    def status(self) -> Esp32TransportStatus:
        """Return current connection and transport counters."""
        ...


class AckTimeoutError(TimeoutError):
    pass


class Esp32ProtocolError(ValueError):
    pass


class ByteTransport(Protocol):
    async def read(self) -> bytes:
        """Return the next received byte chunk, or b'' when the transport closes."""
        ...

    async def write(self, data: bytes) -> None:
        """Write bytes to the peer."""
        ...

    async def close(self) -> None:
        """Close the transport."""
        ...


def monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def uuid7_now() -> UUID:
    timestamp_ms = int(time.time_ns() // 1_000_000) & ((1 << 48) - 1)
    rand_a = random.getrandbits(12)
    rand_b = random.getrandbits(62)
    value = (timestamp_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return UUID(int=value)


def encode_wire_message(msg: WireMessage) -> bytes:
    encoded = json.dumps(msg.to_wire_dict(), separators=(",", ":"), sort_keys=True).encode()
    if len(encoded) > MAX_FRAME_BYTES:
        raise Esp32ProtocolError(f"wire frame exceeds {MAX_FRAME_BYTES} bytes")
    return encoded + b"\n"


def decode_wire_message(line: bytes) -> WireMessage:
    if len(line) > MAX_FRAME_BYTES:
        raise Esp32ProtocolError(f"wire frame exceeds {MAX_FRAME_BYTES} bytes")
    try:
        decoded_raw: object = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Esp32ProtocolError("malformed wire JSON") from exc
    if not isinstance(decoded_raw, dict):
        raise Esp32ProtocolError("wire frame must be a JSON object")
    decoded = cast(dict[str, object], decoded_raw)
    payload_raw = decoded.get("p")
    if not isinstance(payload_raw, dict):
        raise Esp32ProtocolError("wire payload must be an object")
    payload = cast(dict[str, object], payload_raw)
    version = decoded.get("v")
    seq = decoded.get("seq")
    message_type = decoded.get("t")
    ack = decoded.get("ack")
    if not isinstance(version, int):
        raise Esp32ProtocolError("wire version must be an int")
    if not isinstance(seq, int):
        raise Esp32ProtocolError("wire seq must be an int")
    if not isinstance(message_type, str):
        raise Esp32ProtocolError("wire message type must be a string")
    if ack is not None and not isinstance(ack, int):
        raise Esp32ProtocolError("wire ack must be an int or null")
    return WireMessage(v=version, seq=seq, message_type=message_type, ack=ack, payload=payload)


def wire_message_from_event(
    event: DoorboardEvent,
    *,
    seq: int,
    now_mono_ms: int | None = None,
) -> WireMessage:
    event_dump = event.model_dump(mode="python")
    payload = event_dump["payload"]
    event_type = event_dump["type"]
    if event_type == "door.profile_update":
        current_mono_ms = monotonic_ms() if now_mono_ms is None else now_mono_ms
        ttl_ms = max(0, int(payload["expires_at_monotonic_ms"]) - current_mono_ms)
        return WireMessage(
            v=PROTO_V,
            seq=seq,
            message_type="profile_update",
            ack=None,
            payload={
                "profile_id": str(payload["profile_id"]),
                "ttl_ms": ttl_ms,
                "priority": str(payload["priority"]),
            },
        )
    if event_type == "door.profile_clear":
        return WireMessage(
            v=PROTO_V,
            seq=seq,
            message_type="profile_clear",
            ack=None,
            payload={"reason": str(payload["reason"])},
        )
    if event_type == "door.effect_play":
        return WireMessage(
            v=PROTO_V,
            seq=seq,
            message_type="effect_play",
            ack=None,
            payload={
                "effect_id": str(payload["effect_id"]),
                "duration_ms": int(payload["duration_ms"]),
            },
        )
    raise Esp32ProtocolError(f"unsupported outbound ESP32 contract event: {event_type}")


@dataclass(frozen=True)
class Esp32TransportOptions:
    pi_boot_id: str = "door-pi"
    sw_version: str = "doorboard"
    door_id: str = "primary"
    source: str = "esp32-link"
    ack_timeout_ms: int = 50
    max_retries: int = 3
    heartbeat_interval_ms: int = 1_000
    heartbeat_timeout_ms: int = 5_000
    monitor_interval_ms: int = 250
    auto_start_tasks: bool = True
    dedupe_recent_window: int = DEFAULT_DEDUPE_RECENT_WINDOW


class Esp32ProtocolTransport:
    def __init__(
        self,
        byte_transport: ByteTransport,
        *,
        options: Esp32TransportOptions | None = None,
        now_mono_ms: Callable[[], int] = monotonic_ms,
    ) -> None:
        self._bytes = byte_transport
        self._options = options or Esp32TransportOptions()
        self._now_mono_ms = now_mono_ms
        self._seq = 0
        self._fw_version = "unknown"
        self._peer_boot_id: str | None = None
        self._last_heartbeat_mono_ms: int | None = None
        self._connected = False
        self._rx_errors = 0
        self._tx_retries = 0
        self._tx_timeouts = 0
        self._duplicate_rx = 0
        self._protocol_error: str | None = None
        self._closed = False
        self._started = False
        self._line_buffer = bytearray()
        self._discarding_oversize_line = False
        self._dedupe_boot_id: str | None = None
        self._dedupe_high_water_seq = -1
        self._dedupe_recent_seqs: set[int] = set()
        self._pending_acks: dict[int, asyncio.Future[WireMessage]] = {}
        self._events: asyncio.Queue[DoorboardEvent] = asyncio.Queue()
        self._link_states: asyncio.Queue[Esp32LinkState] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._send_lock = asyncio.Lock()

    @classmethod
    async def open_uart(
        cls,
        port: str,
        *,
        baud_rate: int = 115_200,
        options: Esp32TransportOptions | None = None,
    ) -> Self:
        transport = await PosixSerialByteTransport.open(port, baud_rate=baud_rate)
        link = cls(transport, options=options)
        await link.start()
        return link

    @classmethod
    async def open_udp(
        cls,
        *,
        local_addr: tuple[str, int],
        remote_addr: tuple[str, int],
        options: Esp32TransportOptions | None = None,
    ) -> Self:
        transport = await UdpByteTransport.open(local_addr=local_addr, remote_addr=remote_addr)
        link = cls(transport, options=options)
        await link.start()
        return link

    @classmethod
    def from_streams(
        cls,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        options: Esp32TransportOptions | None = None,
    ) -> Self:
        return cls(StreamByteTransport(reader, writer), options=options)

    def make_message(self, message_type: str, payload: Mapping[str, object]) -> WireMessage:
        return WireMessage(
            v=PROTO_V,
            seq=self._next_seq(),
            message_type=message_type,
            ack=None,
            payload=payload,
        )

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._tasks.append(asyncio.create_task(self._read_loop()))
        if self._options.auto_start_tasks:
            self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
            self._tasks.append(asyncio.create_task(self._monitor_loop()))
            with contextlib.suppress(AckTimeoutError):
                await self.send(
                    self.make_message(
                        "hello",
                        {
                            "sw_version": self._options.sw_version,
                            "proto_v": PROTO_V,
                            "boot_id": self._options.pi_boot_id,
                        },
                    )
                )

    async def close(self) -> None:
        self._closed = True
        for task in self._tasks:
            task.cancel()
        for future in self._pending_acks.values():
            if not future.done():
                future.cancel()
        await self._bytes.close()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def send(self, msg: WireMessage) -> WireMessage:
        if msg.message_type not in ACK_REQUIRED:
            await self._write_message(msg)
            return msg
        future = asyncio.get_running_loop().create_future()
        self._pending_acks[msg.seq] = future
        try:
            for attempt in range(self._options.max_retries + 1):
                await self._write_message(msg)
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(future),
                        timeout=self._options.ack_timeout_ms / 1000,
                    )
                except TimeoutError:
                    if attempt == self._options.max_retries:
                        break
                    self._tx_retries += 1
            self._tx_timeouts += 1
            raise AckTimeoutError(f"timed out waiting for ack of seq {msg.seq}")
        finally:
            self._pending_acks.pop(msg.seq, None)

    async def send_event(self, event: DoorboardEvent) -> WireMessage:
        msg = wire_message_from_event(event, seq=self._next_seq(), now_mono_ms=self._now_mono_ms())
        return await self.send(msg)

    def events(self) -> AsyncIterator[DoorboardEvent]:
        return self._event_stream()

    def link_state_events(self) -> AsyncIterator[Esp32LinkState]:
        return self._link_state_stream()

    def status(self) -> Esp32TransportStatus:
        self._apply_heartbeat_timeout()
        return Esp32TransportStatus(
            connected=self._connected,
            last_heartbeat_mono_ms=self._last_heartbeat_mono_ms,
            rx_errors=self._rx_errors,
            tx_retries=self._tx_retries,
        )

    def health_check(self) -> HealthPayload:
        self._apply_heartbeat_timeout()
        if self._protocol_error is not None:
            status = HealthStatus.DEGRADED
            detail = self._protocol_error
        elif self._connected:
            status = HealthStatus.OK
            detail = None
        else:
            status = HealthStatus.DOWN
            detail = "ESP32 heartbeat missing"
        return HealthPayload(service="esp32_link", status=status, detail=detail)

    def metrics(self) -> Esp32TransportMetrics:
        self._apply_heartbeat_timeout()
        return Esp32TransportMetrics(
            connected=1 if self._connected else 0,
            last_heartbeat_mono_ms=self._last_heartbeat_mono_ms or 0,
            rx_errors=self._rx_errors,
            tx_retries=self._tx_retries,
            tx_timeouts=self._tx_timeouts,
            duplicate_rx=self._duplicate_rx,
        )

    def metrics_text(self) -> str:
        metrics = self.metrics()
        values = {
            "esp32_link_connected": metrics.connected,
            "esp32_link_last_heartbeat_mono_ms": metrics.last_heartbeat_mono_ms,
            "esp32_link_rx_errors_total": metrics.rx_errors,
            "esp32_link_tx_retries_total": metrics.tx_retries,
            "esp32_link_tx_timeouts_total": metrics.tx_timeouts,
            "esp32_link_duplicate_rx_total": metrics.duplicate_rx,
        }
        return "".join(f"{name} {value}\n" for name, value in values.items())

    @property
    def inbound_dedupe_entries(self) -> int:
        return len(self._dedupe_recent_seqs)

    async def _event_stream(self) -> AsyncIterator[DoorboardEvent]:
        while True:
            yield await self._events.get()

    async def _link_state_stream(self) -> AsyncIterator[Esp32LinkState]:
        while True:
            yield await self._link_states.get()

    async def _read_loop(self) -> None:
        while not self._closed:
            try:
                chunk = await self._bytes.read()
            except OSError as exc:
                self._protocol_error = f"ESP32 byte transport read failed: {exc}"
                self._set_connected(False, "byte transport read failed")
                return
            if chunk == b"":
                self._set_connected(False, "byte transport closed")
                return
            await self._receive_bytes(chunk)

    async def _heartbeat_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._options.heartbeat_interval_ms / 1000)
            with contextlib.suppress(Exception):
                await self._write_message(
                    self.make_message(
                        "heartbeat",
                        {
                            "uptime_s": self._now_mono_ms() // 1000,
                            "fallback_active": False,
                        },
                    )
                )

    async def _monitor_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._options.monitor_interval_ms / 1000)
            self._apply_heartbeat_timeout()

    async def _receive_bytes(self, data: bytes) -> None:
        for byte in data:
            if byte == 0x0A:
                if self._discarding_oversize_line:
                    self._discarding_oversize_line = False
                    self._line_buffer.clear()
                    continue
                line = bytes(self._line_buffer).rstrip(b"\r")
                self._line_buffer.clear()
                if line:
                    await self._handle_line(line)
                continue
            if self._discarding_oversize_line:
                continue
            self._line_buffer.append(byte)
            if len(self._line_buffer) > MAX_FRAME_BYTES:
                self._rx_errors += 1
                self._protocol_error = f"oversize ESP32 frame over {MAX_FRAME_BYTES} bytes"
                self._line_buffer.clear()
                self._discarding_oversize_line = True

    async def _handle_line(self, line: bytes) -> None:
        try:
            msg = decode_wire_message(line)
        except Esp32ProtocolError as exc:
            self._rx_errors += 1
            self._protocol_error = str(exc)
            return
        if msg.v != PROTO_V:
            self._rx_errors += 1
            self._protocol_error = f"unsupported ESP32 protocol version {msg.v}"
            return
        if msg.message_type == "ack":
            self._handle_ack(msg)
            return
        if msg.message_type in ACK_REQUIRED:
            await self._write_message(
                WireMessage(
                    v=PROTO_V, seq=self._next_seq(), message_type="ack", ack=msg.seq, payload={}
                )
            )

        peer_boot_id = self._peer_boot_id
        if msg.message_type == "hello":
            raw_boot_id = msg.payload.get("boot_id")
            if isinstance(raw_boot_id, str) and raw_boot_id != self._peer_boot_id:
                peer_boot_id = raw_boot_id
                self._peer_boot_id = raw_boot_id
            raw_fw_version = msg.payload.get("fw_version")
            if isinstance(raw_fw_version, str):
                self._fw_version = raw_fw_version
            self._last_heartbeat_mono_ms = self._now_mono_ms()
            self._set_connected(True, "hello")
        if msg.message_type == "heartbeat":
            self._last_heartbeat_mono_ms = self._now_mono_ms()
            self._set_connected(True, "heartbeat")

        if self._is_duplicate_inbound(peer_boot_id or "unknown", msg.seq):
            self._duplicate_rx += 1
            return

        if msg.message_type in INBOUND_EVENT_TYPES:
            try:
                event = self._to_contract_event(msg)
            except (KeyError, Esp32ProtocolError, ValidationError) as exc:
                self._rx_errors += 1
                self._protocol_error = f"invalid ESP32 {msg.message_type} payload: {exc}"
                return
            await self._events.put(event)

    def _handle_ack(self, msg: WireMessage) -> None:
        if msg.ack is None:
            self._rx_errors += 1
            self._protocol_error = "ack frame missing ack sequence"
            return
        future = self._pending_acks.get(msg.ack)
        if future is not None and not future.done():
            future.set_result(msg)

    def _to_contract_event(self, msg: WireMessage) -> DoorboardEvent:
        if msg.message_type == "button_event":
            event_type = "door.button_pressed"
            payload = {
                "press_id": msg.payload["press_id"],
                "had_cached_profile": msg.payload["had_cached_profile"],
                "profile_id": msg.payload["profile_id"],
            }
        elif msg.message_type == "knock_event":
            event_type = "door.knock_detected"
            payload = dict(msg.payload)
        elif msg.message_type == "contact_event":
            event_type = "door.contact_changed"
            payload = dict(msg.payload)
        elif msg.message_type == "heartbeat":
            event_type = "door.controller_health"
            payload = {
                "uptime_s": msg.payload["uptime_s"],
                "fw_version": self._fw_version,
                "cached_profile_id": msg.payload.get("cached_profile_id"),
                "fallback_active": msg.payload["fallback_active"],
            }
        else:
            raise Esp32ProtocolError(f"unsupported inbound ESP32 message: {msg.message_type}")
        return EVENT_ADAPTER.validate_python(
            {
                "event_id": uuid7_now(),
                "type": event_type,
                "source": self._options.source,
                "occurred_at": datetime.now(UTC),
                "monotonic_ms": self._now_mono_ms(),
                "door_id": self._options.door_id,
                "trace_id": uuid7_now(),
                "payload": payload,
            }
        )

    def _is_duplicate_inbound(self, boot_id: str, seq: int) -> bool:
        if boot_id != self._dedupe_boot_id:
            self._dedupe_boot_id = boot_id
            self._dedupe_high_water_seq = -1
            self._dedupe_recent_seqs.clear()

        window = max(1, self._options.dedupe_recent_window)
        high_water = self._dedupe_high_water_seq
        if seq <= high_water - window:
            return True
        if seq in self._dedupe_recent_seqs:
            return True

        if seq > high_water:
            self._dedupe_high_water_seq = seq
            high_water = seq
        self._dedupe_recent_seqs.add(seq)
        cutoff = high_water - window
        if len(self._dedupe_recent_seqs) > window:
            self._dedupe_recent_seqs = {
                recent_seq for recent_seq in self._dedupe_recent_seqs if recent_seq > cutoff
            }
        return False

    async def _write_message(self, msg: WireMessage) -> None:
        async with self._send_lock:
            await self._bytes.write(encode_wire_message(msg))

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _set_connected(self, connected: bool, reason: str) -> None:
        if self._connected == connected:
            return
        self._connected = connected
        self._link_states.put_nowait(
            Esp32LinkState(
                connected=connected,
                changed_at_mono_ms=self._now_mono_ms(),
                reason=reason,
            )
        )

    def _apply_heartbeat_timeout(self) -> None:
        if not self._connected:
            return
        if self._last_heartbeat_mono_ms is None:
            return
        if self._now_mono_ms() - self._last_heartbeat_mono_ms > self._options.heartbeat_timeout_ms:
            self._set_connected(False, "heartbeat timeout")


class StreamByteTransport:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    async def read(self) -> bytes:
        return await self._reader.read(1024)

    async def write(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()


class UdpByteTransport(asyncio.DatagramProtocol):
    def __init__(self, remote_addr: tuple[str, int]) -> None:
        self._remote_addr = remote_addr
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._transport: asyncio.DatagramTransport | None = None

    @classmethod
    async def open(
        cls,
        *,
        local_addr: tuple[str, int],
        remote_addr: tuple[str, int],
    ) -> UdpByteTransport:
        loop = asyncio.get_running_loop()
        protocol = cls(remote_addr)
        transport, _ = await loop.create_datagram_endpoint(lambda: protocol, local_addr=local_addr)
        protocol._transport = transport
        return protocol

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if addr == self._remote_addr:
            self._queue.put_nowait(data)

    def connection_lost(self, exc: Exception | None) -> None:
        self._queue.put_nowait(b"")

    async def read(self) -> bytes:
        return await self._queue.get()

    async def write(self, data: bytes) -> None:
        if self._transport is None:
            raise ConnectionError("UDP transport is not open")
        self._transport.sendto(data, self._remote_addr)

    async def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None


class PosixSerialByteTransport:
    _BAUD_RATES = {
        9_600: termios.B9600,
        19_200: termios.B19200,
        38_400: termios.B38400,
        57_600: termios.B57600,
        115_200: termios.B115200,
        230_400: getattr(termios, "B230400", termios.B115200),
        460_800: getattr(termios, "B460800", termios.B115200),
        921_600: getattr(termios, "B921600", termios.B115200),
    }

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._closed = False
        self._loop.add_reader(self._fd, self._read_ready)

    @classmethod
    async def open(cls, port: str, *, baud_rate: int) -> PosixSerialByteTransport:
        if baud_rate not in cls._BAUD_RATES:
            raise ValueError(f"unsupported UART baud rate: {baud_rate}")
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        cls._configure(fd, baud_rate)
        return cls(fd)

    async def read(self) -> bytes:
        return await self._queue.get()

    async def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            try:
                written = os.write(self._fd, view)
            except BlockingIOError:
                await asyncio.sleep(0)
                continue
            view = view[written:]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.remove_reader(self._fd)
        os.close(self._fd)
        self._queue.put_nowait(b"")

    def _read_ready(self) -> None:
        try:
            data = os.read(self._fd, 1024)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        self._queue.put_nowait(data)

    @classmethod
    def _configure(cls, fd: int, baud_rate: int) -> None:
        attrs = termios.tcgetattr(fd)
        baud = cls._BAUD_RATES[baud_rate]
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8
        attrs[3] = 0
        attrs[4] = baud
        attrs[5] = baud
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        with contextlib.suppress(OSError):
            termios.tcflush(fd, termios.TCIOFLUSH)


async def open_socketpair_streams() -> tuple[
    asyncio.StreamReader,
    asyncio.StreamWriter,
    asyncio.StreamReader,
    asyncio.StreamWriter,
]:
    left, right = socket.socketpair()
    left.setblocking(False)
    right.setblocking(False)
    left_reader, left_writer = await asyncio.open_connection(sock=left)
    right_reader, right_writer = await asyncio.open_connection(sock=right)
    return left_reader, left_writer, right_reader, right_writer


def health_status_literal(payload: HealthPayload) -> Literal["ok", "degraded", "down"]:
    return payload.status.value
