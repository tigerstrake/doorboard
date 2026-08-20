"""Keep door-api attached to the ESP32 door controller.

`packages/esp32-link` has had a working `open_uart()` since T-102, and door-api has
had somewhere to put the result since T-401 (`DoorApiState.esp32_transport`), but
nothing ever joined the two. The field stayed `None` in every deployment; only
tests and the simulator injected a transport; and `ESP32_TRANSPORT` /
`ESP32_UART_DEVICE` were read by no service at all despite being in
`.env.example`. On real hardware a bell press had nowhere to go. This module is
the missing wiring.

Three rules shape it, in priority order.

**Startup must never depend on the controller.** The link is opened by a
background task that is not awaited, exactly as `MqttBridge` does it. A missing,
unplugged, or unflashed board must not delay or fail door-api: the DoorPad,
wallboard and visitor surfaces all work without it, and ARCHITECTURE.md §4 does
not allow anything new to sit in front of the door interaction path.

**A quiet controller is not a broken cable.** `Esp32ProtocolTransport` reports
`connected=False` for two situations that need opposite responses:

  - `byte transport closed` / `byte transport read failed` — the descriptor is
    gone (board unplugged, USB re-enumerated). Only reopening recovers.
  - `heartbeat timeout` — the descriptor is fine; the controller simply has not
    spoken for `heartbeat_timeout_ms`. Reopening *here* would be actively
    harmful: on an ESP32-S3-DevKitC the bridge's DTR line drives EN, so opening
    the port resets the controller, discarding the cached profile it holds
    precisely so it can answer the button during a Pi outage (ADR-0006). The
    transport recovers on its own once frames resume, so this case is counted and
    waited out.

**Reconnect cost stays bounded.** Capped exponential backoff, so a permanently
dead port cannot spin the CPU on a device that is also running face recognition.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from doorboard_esp32_link import Esp32ProtocolTransport, Esp32TransportOptions

logger = logging.getLogger("door_api.esp32_link")

# Disconnect reasons that mean the file descriptor itself is unusable. Anything
# else (today: "heartbeat timeout") leaves the port open deliberately — see the
# module docstring.
REOPEN_REASONS: Final[frozenset[str]] = frozenset(
    {"byte transport closed", "byte transport read failed"}
)

TRANSPORT_KINDS: Final[frozenset[str]] = frozenset({"uart", "udp", "mock"})

Opener = Callable[[], Awaitable[Esp32ProtocolTransport]]


def parse_addr(raw: str) -> tuple[str, int] | None:
    """Parse ``host:port``, returning None for blank or malformed input."""
    host, separator, port = raw.strip().rpartition(":")
    if not separator or not host or not port.isdigit():
        return None
    return host, int(port)


@dataclass(frozen=True, kw_only=True)
class Esp32LinkSettings:
    """Where the controller is and how hard to try reaching it."""

    transport: str
    uart_device: str
    uart_baud: int
    udp_local_addr: str
    udp_remote_addr: str
    reconnect_base_s: float
    reconnect_max_s: float
    door_id: str

    def __post_init__(self) -> None:
        # Reported here rather than from `kind`, which several callers read: a
        # property that logs produces one line per access, so the same typo was
        # announced two or three times per startup.
        if self.transport not in TRANSPORT_KINDS:
            logger.error(
                "esp32_link_unknown_transport",
                extra={"transport": self.transport, "supported": sorted(TRANSPORT_KINDS)},
            )

    @property
    def kind(self) -> str:
        """The transport kind, with anything unrecognised treated as absent.

        Failing closed rather than guessing: a typo in `ESP32_TRANSPORT` should
        leave the door working, not open a port nobody asked for.
        """
        return self.transport if self.transport in TRANSPORT_KINDS else "mock"

    @property
    def enabled(self) -> bool:
        return self.kind in {"uart", "udp"}

    def describe(self) -> str:
        if self.kind == "uart":
            return f"uart:{self.uart_device}@{self.uart_baud}"
        if self.kind == "udp":
            return f"udp:{self.udp_local_addr}->{self.udp_remote_addr}"
        return self.kind

    def options(self) -> Esp32TransportOptions:
        return Esp32TransportOptions(door_id=self.door_id, source="door-api")

    def opener(self) -> Opener | None:
        """Build the coroutine factory that opens this link, or None if disabled."""
        if self.kind == "uart":

            async def open_uart() -> Esp32ProtocolTransport:
                return await Esp32ProtocolTransport.open_uart(
                    self.uart_device,
                    baud_rate=self.uart_baud,
                    options=self.options(),
                )

            return open_uart

        if self.kind == "udp":
            local = parse_addr(self.udp_local_addr)
            remote = parse_addr(self.udp_remote_addr)
            if local is None or remote is None:
                logger.error(
                    "esp32_link_udp_addresses_invalid",
                    extra={"local": self.udp_local_addr, "remote": self.udp_remote_addr},
                )
                return None

            async def open_udp() -> Esp32ProtocolTransport:
                return await Esp32ProtocolTransport.open_udp(
                    local_addr=local,
                    remote_addr=remote,
                    options=self.options(),
                )

            return open_udp

        return None


class Esp32LinkSupervisor:
    """Open the controller link, hand it to door-api, and reopen it when it dies.

    `attach` receives a live transport; `detach` is called before any reopen and
    on shutdown. Both are synchronous so the caller can keep its own state
    transitions simple — the supervisor owns all the awaiting.
    """

    def __init__(
        self,
        *,
        opener: Opener,
        attach: Callable[[Esp32ProtocolTransport], None],
        detach: Callable[[], None],
        target: str,
        reconnect_base_s: float = 1.0,
        reconnect_max_s: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._opener = opener
        self._attach = attach
        self._detach = detach
        self._target = target
        self._reconnect_base_s = max(0.0, reconnect_base_s)
        self._reconnect_max_s = max(self._reconnect_base_s, reconnect_max_s)
        self._sleep = sleep

        self.connected = False
        self.connects = 0
        self.reopens = 0
        self.open_failures = 0
        self.idle_timeouts = 0

    async def run(self) -> None:
        """Hold the link open forever. Only ``CancelledError`` escapes."""
        backoff = self._reconnect_base_s
        while True:
            try:
                link = await self._opener()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.open_failures += 1
                logger.warning(
                    "esp32_link_open_failed",
                    extra={"target": self._target, "retry_in_s": backoff},
                    exc_info=True,
                )
                await self._sleep(backoff)
                backoff = min(self._reconnect_max_s, max(backoff * 2, self._reconnect_base_s))
                continue

            logger.info("esp32_link_opened", extra={"target": self._target})
            connects_before = self.connects
            self._attach(link)
            try:
                await self._watch(link)
            except Exception:
                # CancelledError is deliberately not caught: `finally` performs the
                # same detach-and-close, and catching it here as well closed the
                # transport twice on shutdown.
                logger.warning(
                    "esp32_link_watch_failed", extra={"target": self._target}, exc_info=True
                )
            finally:
                self.connected = False
                self._detach()
                with contextlib.suppress(Exception):
                    await link.close()

            self.reopens += 1
            # A link that actually handshook earns a cheap retry: the target is
            # known good, so this is a cable or a reboot, not a wrong device path.
            backoff = (
                self._reconnect_base_s
                if self.connects > connects_before
                else min(self._reconnect_max_s, max(backoff * 2, self._reconnect_base_s))
            )
            await self._sleep(backoff)

    async def _watch(self, link: Esp32ProtocolTransport) -> None:
        """Return once the link needs reopening; keep waiting while it is merely idle."""
        states = link.link_state_events()
        try:
            async for state in states:
                if state.connected:
                    self.connects += 1
                    self.connected = True
                    logger.info(
                        "esp32_link_connected",
                        extra={"target": self._target, "reason": state.reason},
                    )
                    continue

                self.connected = False
                if state.reason in REOPEN_REASONS:
                    logger.warning(
                        "esp32_link_lost",
                        extra={"target": self._target, "reason": state.reason},
                    )
                    return

                # Descriptor still good: do not reset the controller over silence.
                self.idle_timeouts += 1
                logger.warning(
                    "esp32_link_idle",
                    extra={"target": self._target, "reason": state.reason},
                )
        finally:
            await states.aclose()

    def metrics(self) -> dict[str, int]:
        return {
            "door_api_esp32_link_connected": int(self.connected),
            "door_api_esp32_link_connects_total": self.connects,
            "door_api_esp32_link_reopens_total": self.reopens,
            "door_api_esp32_link_open_failures_total": self.open_failures,
            "door_api_esp32_link_idle_timeouts_total": self.idle_timeouts,
        }
