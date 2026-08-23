"""Carry door-visiond's identity events to door-api, which owns the display path.

Recognition already worked end to end *inside* door-visiond: a stable match wrote
the ``current_visitor`` cache, pushed ``door.profile_update`` to the ESP32, and
emitted ``vision.identity_stable`` onto the in-process broadcast queue.  Nothing
drained that queue and no transport left the process, so the event existed only in
door-visiond's log.  door-api's session machine never saw it, never left ``IDLE``,
and ``ApproachGreeting`` only renders in ``APPROACH_DETECTED``/``IDENTITY_CACHED`` —
so a recognised person got the door light and a silent screen.  ADR-0018 §3 calls
the wallboard greeting "the entire point of the feature" and T-303 lists the UI
greeting path as a deliverable; both assumed this hop existed.

It looked implemented from every angle that did not involve walking up to the door:
the simulator publishes onto its own in-process bus, and the dev UI's mock trigger
notifies browser-local listeners directly (door-api's ``/ws`` accepts only
``subscribe`` frames, so even that publish is dropped server-side).

Transport is loopback HTTP, mirroring the existing door-visiond → door-sync purge
delivery: no new dependency, and no broker in the critical path (ARCHITECTURE.md §7
keeps MQTT out of it).  Delivery is deliberately **best-effort**, matching live UI
fan-out semantics: the recognition loop must never wait on the screen path, so a
slow or dead door-api costs a dropped event and a log line, never a frame.  A
dropped ``identity_expired`` self-heals — door-api's ``approach_timeout_s`` returns
the session to ``IDLE`` on its own.

Only identity events are forwarded.  ``vision.face_visible`` fires on every frame
holding a face; door-api does nothing with it and door-ui does not subscribe to it,
so shipping it would be pure loopback traffic at frame rate.
"""

from __future__ import annotations

import asyncio
import contextlib
import urllib.request
from typing import Protocol

from doorboard_contracts.events import DoorboardEvent

from door_visiond.events import get_broadcast_queue
from door_visiond.logging_setup import get_logger

logger = get_logger("door_visiond.event_forwarder")

# door-api reaches the screen from these two and nothing else here does.
FORWARDED_EVENT_TYPES: frozenset[str] = frozenset(
    {"vision.identity_stable", "vision.identity_expired"}
)


class EventTransport(Protocol):
    def send(self, event: DoorboardEvent) -> None:
        """Deliver one event, blocking. Raise on any failure."""
        ...


class HttpEventTransport:
    """POST one contract event to door-api's internal ingest, blocking."""

    def __init__(self, *, base_url: str, token: str, timeout_s: float = 1.0) -> None:
        self._url = f"{base_url.rstrip('/')}/internal/events"
        self._token = token
        self._timeout_s = timeout_s

    def send(self, event: DoorboardEvent) -> None:
        body = event.model_dump_json().encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - fixed loopback URL from settings
            self._url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout_s) as response:  # noqa: S310
            if not 200 <= response.status < 300:
                msg = f"door-api event ingest returned HTTP {response.status}"
                raise RuntimeError(msg)


class ProfileTransport(Protocol):
    def send(self, event: DoorboardEvent) -> None:
        """Deliver one profile push, blocking. Raise on any failure."""
        ...


class HttpProfileTransport:
    """POST a recognition profile push to door-api's ESP32 relay, blocking.

    door-api owns the single ESP32 UART, so door-visiond's ``door.profile_update`` /
    ``door.profile_clear`` reach the controller by being forwarded here rather than
    sent direct (ADR-0040). Same loopback, same best-effort semantics, and — unlike
    the ESP32 wire — no seq or expiry conversion here: the raw contract event goes
    over, and door-api does the conversion when it owns the wire.
    """

    def __init__(self, *, base_url: str, token: str, timeout_s: float = 1.0) -> None:
        self._url = f"{base_url.rstrip('/')}/internal/esp32/profile"
        self._token = token
        self._timeout_s = timeout_s

    def send(self, event: DoorboardEvent) -> None:
        body = event.model_dump_json().encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - fixed loopback URL from settings
            self._url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout_s) as response:  # noqa: S310
            if not 200 <= response.status < 300:
                msg = f"door-api esp32 profile relay returned HTTP {response.status}"
                raise RuntimeError(msg)


class EventForwarder:
    """Drains the broadcast queue and ships identity events to door-api."""

    def __init__(
        self,
        transport: EventTransport,
        *,
        retry_backoff_s: float = 2.0,
        retry_backoff_max_s: float = 30.0,
    ) -> None:
        self._transport = transport
        self._retry_backoff_s = retry_backoff_s
        self._retry_backoff_max_s = retry_backoff_max_s
        self._task: asyncio.Task[None] | None = None
        # While this deadline is in the future, events are dropped without a send
        # attempt, so an unreachable door-api costs one timeout per backoff window
        # instead of one per event.
        self._quiet_until = 0.0
        self._backoff_s = 0.0
        self.forwarded = 0
        self.dropped = 0
        self.failures = 0
        self.last_error: str | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="visiond-event-forwarder")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        queue = get_broadcast_queue()
        loop = asyncio.get_running_loop()
        while True:
            event = await queue.get()
            try:
                if event.type not in FORWARDED_EVENT_TYPES:
                    continue
                # Dropped rather than queued for later: a greeting is worthless once
                # the person has walked away, and a backlog delivered on reconnect
                # would replay stale identities into the session machine.
                if loop.time() < self._quiet_until:
                    self.dropped += 1
                    continue
                try:
                    await asyncio.to_thread(self._transport.send, event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.failures += 1
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    self._backoff_s = min(
                        self._retry_backoff_max_s,
                        max(self._retry_backoff_s, self._backoff_s * 2),
                    )
                    self._quiet_until = loop.time() + self._backoff_s
                    logger.warning(
                        "event_forward_failed",
                        extra={
                            "event_type": event.type,
                            "error_class": type(exc).__name__,
                            "backoff_s": self._backoff_s,
                        },
                    )
                else:
                    self.forwarded += 1
                    self._backoff_s = 0.0
                    self._quiet_until = 0.0
            finally:
                queue.task_done()
