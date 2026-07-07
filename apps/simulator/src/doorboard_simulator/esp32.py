from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from uuid import UUID

from door_api.adapters import Esp32TransportStatus, WireMessage
from doorboard_contracts import DoorboardEvent

from doorboard_simulator.clock import SimClock
from doorboard_simulator.events import EventFactory

ACK_REQUIRED = {
    "hello",
    "profile_update",
    "profile_clear",
    "effect_play",
    "button_event",
    "knock_event",
    "contact_event",
}


@dataclass(frozen=True)
class CachedProfile:
    profile_id: str
    expires_at_mono_ms: int
    priority: str


class AckTimeoutError(TimeoutError):
    pass


class FakeEsp32Transport:
    proto_v = 1
    max_retries = 3
    retry_spacing_ms = 50
    heartbeat_timeout_ms = 5_000

    def __init__(self, clock: SimClock, events: EventFactory) -> None:
        self._clock = clock
        self._events = events
        self.boot_id = "esp32-sim-boot-1"
        self.fw_version = "sim-0.1.0"
        self._seq = 0
        self._rx_errors = 0
        self._tx_retries = 0
        self._last_pi_heartbeat_mono_ms: int | None = None
        self._connected = False
        self._fallback_active = True
        self._cached_profile: CachedProfile | None = None
        self._seen_pi_messages: set[tuple[str, int]] = set()
        self._side_effects: list[str] = []
        self._inbound: asyncio.Queue[WireMessage] = asyncio.Queue()
        self._drop_acks = 0
        self._press_counter = 0

    @property
    def side_effects(self) -> tuple[str, ...]:
        return tuple(self._side_effects)

    @property
    def fallback_active(self) -> bool:
        self._apply_timeouts()
        return self._fallback_active

    @property
    def cached_profile_id(self) -> str | None:
        self._apply_timeouts()
        return self._cached_profile.profile_id if self._cached_profile is not None else None

    def drop_next_acks(self, count: int) -> None:
        self._drop_acks = count

    def make_message(self, message_type: str, payload: Mapping[str, object]) -> WireMessage:
        self._seq += 1
        return WireMessage(
            v=self.proto_v, seq=self._seq, message_type=message_type, ack=None, payload=payload
        )

    async def send(self, msg: WireMessage) -> WireMessage:
        for attempt in range(self.max_retries + 1):
            ack = self.receive_from_pi(msg, sender_boot_id="pi-sim-boot")
            if ack is not None:
                return ack
            if attempt == self.max_retries:
                break
            self._tx_retries += 1
            self._clock.advance_by(self.retry_spacing_ms)
        raise AckTimeoutError(f"timed out waiting for ack of seq {msg.seq}")

    def receive_from_pi(self, msg: WireMessage, *, sender_boot_id: str) -> WireMessage | None:
        self._apply_timeouts()
        if not self._valid_wire_message(msg):
            self._rx_errors += 1
            return None

        key = (sender_boot_id, msg.seq)
        duplicate = key in self._seen_pi_messages
        self._seen_pi_messages.add(key)

        if not duplicate:
            self._apply_pi_message(msg)

        if msg.message_type not in ACK_REQUIRED:
            return None
        if self._drop_acks > 0:
            self._drop_acks -= 1
            return None
        return WireMessage(
            v=self.proto_v, seq=self._seq, message_type="ack", ack=msg.seq, payload={}
        )

    async def emit_button_press(self) -> WireMessage:
        self._apply_timeouts()
        self._press_counter += 1
        profile_id = self.cached_profile_id
        msg = self._next_esp_message(
            "button_event",
            {
                "press_id": str(UUID(int=self._press_counter)),
                "pressed_at_mono_ms": self._clock.monotonic_ms,
                "had_cached_profile": profile_id is not None,
                "profile_id": profile_id,
            },
        )
        await self._inbound.put(msg)
        self._side_effects.append("generic_feedback")
        if profile_id is not None:
            self._side_effects.append(f"personalized_feedback:{profile_id}")
        return msg

    async def emit_knock(self, *, pattern_id: str, confidence: float) -> WireMessage:
        msg = self._next_esp_message(
            "knock_event",
            {"pattern_id": pattern_id, "confidence": confidence},
        )
        await self._inbound.put(msg)
        return msg

    async def heartbeat_from_esp32(self) -> WireMessage:
        msg = self._next_esp_message(
            "heartbeat",
            {
                "uptime_s": self._clock.monotonic_ms // 1000,
                "fallback_active": self.fallback_active,
                "cached_profile_id": self.cached_profile_id,
            },
        )
        await self._inbound.put(msg)
        return msg

    def events(self) -> AsyncIterator[DoorboardEvent]:
        return self._event_stream()

    async def _event_stream(self) -> AsyncIterator[DoorboardEvent]:
        while True:
            yield self.to_contract_event(await self._inbound.get())

    def status(self) -> Esp32TransportStatus:
        self._apply_timeouts()
        return Esp32TransportStatus(
            connected=self._connected,
            last_heartbeat_mono_ms=self._last_pi_heartbeat_mono_ms,
            rx_errors=self._rx_errors,
            tx_retries=self._tx_retries,
        )

    def to_contract_event(self, msg: WireMessage) -> DoorboardEvent:
        if msg.message_type == "button_event":
            return self._events.make(
                "door.button_pressed",
                {
                    "press_id": msg.payload["press_id"],
                    "had_cached_profile": msg.payload["had_cached_profile"],
                    "profile_id": msg.payload["profile_id"],
                },
            )
        if msg.message_type == "knock_event":
            return self._events.make("door.knock_detected", msg.payload)
        if msg.message_type == "heartbeat":
            return self._events.make(
                "door.controller_health",
                {
                    "uptime_s": msg.payload["uptime_s"],
                    "fw_version": self.fw_version,
                    "cached_profile_id": msg.payload["cached_profile_id"],
                    "fallback_active": msg.payload["fallback_active"],
                },
            )
        msg_text = f"unsupported inbound ESP32 message: {msg.message_type}"
        raise ValueError(msg_text)

    def _next_esp_message(self, message_type: str, payload: Mapping[str, object]) -> WireMessage:
        self._seq += 1
        return WireMessage(
            v=self.proto_v, seq=self._seq, message_type=message_type, ack=None, payload=payload
        )

    def _valid_wire_message(self, msg: WireMessage) -> bool:
        if msg.v != self.proto_v:
            return False
        encoded = json.dumps(msg.to_wire_dict(), separators=(",", ":"), sort_keys=True)
        return len(encoded.encode("utf-8")) <= 512

    def _apply_pi_message(self, msg: WireMessage) -> None:
        payload = msg.payload
        if msg.message_type == "hello":
            self._connected = True
            self._fallback_active = False
            self._last_pi_heartbeat_mono_ms = self._clock.monotonic_ms
            self._side_effects.append("hello")
            return
        if msg.message_type == "heartbeat":
            self._connected = True
            self._fallback_active = False
            self._last_pi_heartbeat_mono_ms = self._clock.monotonic_ms
            return
        if msg.message_type == "profile_update":
            raw_ttl_ms = payload["ttl_ms"]
            ttl_ms = raw_ttl_ms if isinstance(raw_ttl_ms, int) else int(str(raw_ttl_ms))
            self._cached_profile = CachedProfile(
                profile_id=str(payload["profile_id"]),
                expires_at_mono_ms=self._clock.monotonic_ms + ttl_ms,
                priority=str(payload["priority"]),
            )
            self._side_effects.append(f"profile_update:{self._cached_profile.profile_id}")
            return
        if msg.message_type == "profile_clear":
            self._cached_profile = None
            self._side_effects.append(f"profile_clear:{payload['reason']}")
            return
        if msg.message_type == "effect_play":
            self._side_effects.append(f"effect_play:{payload['effect_id']}")

    def _apply_timeouts(self) -> None:
        if (
            self._cached_profile is not None
            and self._clock.monotonic_ms >= self._cached_profile.expires_at_mono_ms
        ):
            self._cached_profile = None
        if (
            self._last_pi_heartbeat_mono_ms is None
            or self._clock.monotonic_ms - self._last_pi_heartbeat_mono_ms
            > self.heartbeat_timeout_ms
        ):
            self._fallback_active = True
