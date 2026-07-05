from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Protocol


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


class Esp32Transport(Protocol):
    async def send(self, msg: WireMessage) -> WireMessage:
        """Send a wire message and resolve with its ack or raise on timeout."""
        ...

    def events(self) -> AsyncIterator[WireMessage]:
        """Yield inbound ESP32 wire messages translated once at this boundary."""
        ...

    def status(self) -> Esp32TransportStatus:
        """Return current connection and transport counters."""
        ...
