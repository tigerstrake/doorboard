"""Carry door-media's storage telemetry to door-api, which owns the display path.

door-media emits ``media.storage_status`` every 30 s: free bytes, sync queue depth, oldest
unsynced age, and whether recording is still allowed. door-ui *subscribes* to it and renders a
capacity card from it. Nothing carried the event between the two.

The plumbing runs one way. door-api forwards session events **to** door-media over
``/internal/session_event``, and door-media's own events go onto an in-process broadcast queue
whose only reader is an admin-authenticated SSE stream at ``GET /events`` that nothing
consumes. So the card sat on "Waiting for a media.storage_status update; no capacity is being
guessed." permanently — honest, and permanently uninformative.

Exactly the shape of the greeting bug that ``door_visiond.event_forwarder`` was written to fix,
so this mirrors it rather than inventing a second pattern: loopback HTTP to
``/internal/events``, no new dependency, no broker in the critical path (ARCHITECTURE.md §7).

**Best-effort, and deliberately lossy.** Recording, retention and the sync queue must never
wait on the screen path, so a slow or dead door-api costs a dropped event and a log line. A
dropped storage status self-heals by construction: another one is emitted on the next interval,
and the card is a snapshot rather than a ledger. That is also why a failure sets a quiet window
— an unreachable door-api costs one timeout per window instead of one per event.

Only storage status is forwarded. The other media events assert that a recording exists, was
finalized, or was deleted, which is a claim about durable state that belongs in the sync path
(door-sync → NUC), not a fan-out to a screen.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import urllib.request
from typing import Protocol

from doorboard_contracts.events import DoorboardEvent

from door_media.emitter import subscribe_broadcast_queue, unsubscribe_broadcast_queue

logger = logging.getLogger("door_media.event_forwarder")

# door-ui reads the capacity card from this and nothing else here reaches a screen.
FORWARDED_EVENT_TYPES: frozenset[str] = frozenset({"media.storage_status"})


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


class EventForwarder:
    """Drains a broadcast subscription and ships storage telemetry to door-api."""

    def __init__(
        self,
        transport: EventTransport,
        *,
        retry_backoff_s: float = 2.0,
        retry_backoff_max_s: float = 60.0,
    ) -> None:
        self._transport = transport
        self._retry_backoff_s = retry_backoff_s
        self._retry_backoff_max_s = retry_backoff_max_s
        self._task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[DoorboardEvent] | None = None
        # While this deadline is in the future, events are dropped without a send attempt, so
        # an unreachable door-api costs one timeout per backoff window, not one per event.
        self._quiet_until = 0.0
        self._backoff_s = 0.0
        self.forwarded = 0
        self.dropped = 0
        self.failures = 0
        self.last_error: str | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            # A dedicated subscription, not the shared queue: the SSE route drains that one,
            # and two consumers competing for the same events would each see roughly half.
            self._queue = subscribe_broadcast_queue()
            self._task = asyncio.create_task(self._loop(), name="door-media-event-forwarder")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        if self._queue is not None:
            unsubscribe_broadcast_queue(self._queue)
            self._queue = None

    async def _loop(self) -> None:
        queue = self._queue
        if queue is None:  # pragma: no cover - start() always sets it
            return
        loop = asyncio.get_running_loop()
        while True:
            event = await queue.get()
            try:
                if event.type not in FORWARDED_EVENT_TYPES:
                    continue
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
                with contextlib.suppress(ValueError):
                    queue.task_done()
