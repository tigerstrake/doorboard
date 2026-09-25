from __future__ import annotations

import asyncio
import json
import secrets
from collections import deque
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from uuid import UUID

from door_api.adapters import Esp32TransportStatus, WireMessage
from doorboard_contracts import DoorboardEvent
from doorboard_esp32_link import wire_message_from_event

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

# The firmware remembers the last 16 (boot_id, seq) pairs it saw from the Pi
# (door_protocol.c `seen[16]`); an older seq reused after that is applied again.
SEEN_RING_ENTRIES = 16
CONSTANT_BOOT_ID = "esp32-sim-boot-1"


@dataclass(frozen=True)
class CachedProfile:
    profile_id: str
    expires_at_mono_ms: int
    priority: str


class AckTimeoutError(TimeoutError):
    pass


class FakeEsp32Transport:
    """The controller as the Pi engine sees it, without the hardware.

    Mirrors the firmware's protocol behaviour closely enough that the Pi-side
    engine can be validated against it (tests/integration): a per-boot
    boot_id (or a constant one, to reproduce the reboot-dedupe failure the
    firmware used to have), a bounded dedupe ring, acks that consume a
    sequence number, a hello in answer to the Pi's hello and on the fallback
    edge, and the fallback animation as a state a press shows.
    """

    proto_v = 1
    max_retries = 3
    retry_spacing_ms = 50
    heartbeat_timeout_ms = 5_000

    def __init__(
        self,
        clock: SimClock,
        events: EventFactory,
        *,
        random_boot_id: bool = True,
    ) -> None:
        self._clock = clock
        self._events = events
        self._random_boot_id = random_boot_id
        self.boot_id = self._new_boot_id()
        self.fw_version = "sim-0.1.0"
        self._seq = 0
        self._rx_errors = 0
        self._tx_retries = 0
        self._tx_dropped = 0
        self._last_pi_heartbeat_mono_ms: int | None = None
        self._connected = False
        self._fallback_active = True
        self._fallback_reported = True  # boot state is fallback, not an edge
        self._cached_profile: CachedProfile | None = None
        self._peer_boot_id: str | None = None
        self._seen_pi_messages: deque[tuple[str, int]] = deque(maxlen=SEEN_RING_ENTRIES)
        self._side_effects: list[str] = []
        self._inbound: asyncio.Queue[WireMessage] = asyncio.Queue()
        self._outbound: list[WireMessage] = []
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

    @property
    def seen_pi_messages(self) -> int:
        return len(self._seen_pi_messages)

    def drop_next_acks(self, count: int) -> None:
        self._drop_acks = count

    def reboot(self) -> None:
        """Power-cycle the controller: everything restarts, seq included.

        The boot_id changes only when the fake was built with a random one --
        the constant setting reproduces the firmware that announced the same
        boot_id every boot.
        """
        self.boot_id = self._new_boot_id()
        self._seq = 0
        self._connected = False
        self._fallback_active = True
        self._fallback_reported = True
        self._last_pi_heartbeat_mono_ms = None
        self._cached_profile = None
        self._peer_boot_id = None
        self._seen_pi_messages.clear()
        self._outbound.clear()
        self._side_effects.append("boot")
        self._outbound.append(self.make_hello())

    def make_message(self, message_type: str, payload: Mapping[str, object]) -> WireMessage:
        self._seq += 1
        return WireMessage(
            v=self.proto_v, seq=self._seq, message_type=message_type, ack=None, payload=payload
        )

    def make_hello(self) -> WireMessage:
        return self._next_esp_message(
            "hello",
            {"fw_version": self.fw_version, "proto_v": self.proto_v, "boot_id": self.boot_id},
        )

    def take_outbound(self) -> list[WireMessage]:
        """Frames the controller emitted on its own (hellos), oldest first."""
        frames = list(self._outbound)
        self._outbound.clear()
        return frames

    async def send_event(self, event: DoorboardEvent) -> WireMessage:
        self._seq += 1
        msg = wire_message_from_event(event, seq=self._seq, now_mono_ms=self._clock.monotonic_ms)
        return await self.send(msg)

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
        if msg.message_type == "ack":
            # Acks are matched against pending sends, never deduped or acked.
            return None

        if msg.message_type == "hello" and sender_boot_id != self._peer_boot_id:
            # A Pi we have not seen: its seqs restart at 1, so nothing from the
            # previous Pi run may dedupe them (firmware: forget_seen).
            self._seen_pi_messages.clear()
        key = (sender_boot_id, msg.seq)
        duplicate = key in self._seen_pi_messages
        if not duplicate:
            self._seen_pi_messages.append(key)
            self._apply_pi_message(msg, sender_boot_id)

        if msg.message_type not in ACK_REQUIRED:
            return None
        # The ack is built (and its seq consumed) before the wire can lose it.
        self._seq += 1
        ack = WireMessage(
            v=self.proto_v, seq=self._seq, message_type="ack", ack=msg.seq, payload={}
        )
        if self._drop_acks > 0:
            self._drop_acks -= 1
            return None
        return ack

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
        elif self._fallback_active:
            # Fallback is a state: while the Pi is lost every press shows it.
            self._side_effects.append("fallback_feedback")
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
                "rx_errors": self._rx_errors,
                "tx_retries": self._tx_retries,
                "tx_dropped": self._tx_dropped,
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

    def _new_boot_id(self) -> str:
        if self._random_boot_id:
            return f"esp32-sim-{secrets.token_hex(8)}"
        return CONSTANT_BOOT_ID

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

    def _pi_alive(self) -> None:
        self._connected = True
        self._fallback_active = False
        self._fallback_reported = False
        self._last_pi_heartbeat_mono_ms = self._clock.monotonic_ms

    def _apply_pi_message(self, msg: WireMessage, sender_boot_id: str) -> None:
        payload = msg.payload
        if msg.message_type == "hello":
            self._pi_alive()
            self._peer_boot_id = sender_boot_id
            self._side_effects.append("hello")
            # The Pi learns fw_version and our boot_id and resets its dedupe
            # against a link that has just (re)started.
            self._outbound.append(self.make_hello())
            return
        if msg.message_type == "heartbeat":
            self._pi_alive()
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
        if self._fallback_active and not self._fallback_reported:
            # The transition into fallback: the animation plays once and a
            # hello goes out so a slow Pi re-handshakes without a restart.
            self._fallback_reported = True
            self._side_effects.append("fallback_animation")
            self._outbound.append(self.make_hello())
