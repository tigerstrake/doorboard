"""Outbound relay client for remote enrollment (ADR-0016 §6).

Two pieces:

* ``HttpRelayTransport`` — blocking urllib calls to the Vercel relay, matching the
  pattern the archive-purge outbox already uses.  No new HTTP dependency.
* ``RelayWorker`` — the asyncio task that publishes the door key, re-registers
  open invites, polls for sealed bundles, and acks results.

Binding constraints from ADR-0016 §6, all enforced here:

* **Never in the door path.**  This is its own task with hard per-request
  timeouts; button → ESP32 → local UI cannot be delayed by it.  Nothing in the
  session state machine awaits anything in this file.
* **Outbound only.**  Every exchange is initiated by the Pi.  There is no server
  here, no inbound port, no tunnel — it works behind NAT and CGNAT.
* **Degrades quietly.**  A missing or broken relay produces bounded backoff, a
  metric, and ``relay_status: "degraded"`` in ``/health`` — never an exception
  that escapes the loop, and never a change to service health overall, because
  remote enrollment is a convenience and recognition is not authorization.
* **Respects the privacy kill switch and the storage lock.**  When either is
  active the worker does not collect, so no plaintext is produced.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote

from doorboard_contracts.enrollment_relay import (
    DoorKeyPublication,
    InviteRegistration,
    PickupAck,
    PickupBatch,
    SealedBundle,
)

from door_visiond.logging_setup import get_logger

logger = get_logger("door_visiond.relay_client")

_JSON = {"Content-Type": "application/json"}


class RelayTransportError(RuntimeError):
    """Any relay exchange that did not complete. Carries no user data."""


class RelayTransport(Protocol):
    """The relay operations the worker needs. Implemented over HTTP; faked in tests."""

    def publish_door_key(self, publication: DoorKeyPublication) -> None: ...

    def register_invite(self, registration: InviteRegistration) -> None: ...

    def revoke_invite(self, invite_id: str) -> None: ...

    def poll_pickup(self) -> PickupBatch: ...

    def acknowledge(self, ack: PickupAck) -> None: ...


class HttpRelayTransport:
    """urllib-based transport. Every call has a hard timeout and never retries in place."""

    def __init__(self, *, base_url: str, device_token: str, timeout_s: float) -> None:
        self._base = base_url.rstrip("/")
        self._token = device_token
        self._timeout = timeout_s

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        url = f"{self._base}{path}"
        if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
            # Sealed payloads are already end-to-end encrypted, but the device
            # token and invite metadata must not cross the internet in the clear.
            # Loopback is allowed so tests and local relay runs work.
            msg = "relay base URL must be https (or loopback for local testing)"
            raise RelayTransportError(msg)
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(  # noqa: S310 — scheme validated above
            url,
            data=body,
            headers={"Authorization": f"Bearer {self._token}", **(_JSON if body else {})},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                if not 200 <= response.status < 300:
                    raise RelayTransportError(f"relay returned HTTP {response.status}")
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise RelayTransportError(f"relay returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RelayTransportError(f"relay unreachable: {type(exc).__name__}") from exc
        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RelayTransportError("relay returned malformed JSON") from exc
        if not isinstance(decoded, dict):
            raise RelayTransportError("relay returned a non-object body")
        return decoded

    def publish_door_key(self, publication: DoorKeyPublication) -> None:
        self._request("PUT", "/api/door-key", publication.model_dump(mode="json"))

    def register_invite(self, registration: InviteRegistration) -> None:
        self._request("PUT", "/api/invite", registration.model_dump(mode="json"))

    def revoke_invite(self, invite_id: str) -> None:
        self._request("DELETE", f"/api/invite/{quote(invite_id, safe='')}")

    def poll_pickup(self) -> PickupBatch:
        return PickupBatch.model_validate(self._request("GET", "/api/pickup"))

    def acknowledge(self, ack: PickupAck) -> None:
        self._request("POST", "/api/pickup/ack", ack.model_dump(mode="json"))


class RelayHandler(Protocol):
    """What the worker needs from the service. Keeps privacy policy out of this file."""

    def relay_collection_allowed(self) -> bool:
        """False while privacy mode is on or the enrollment volume is locked (P-18)."""
        ...

    def relay_door_key_publication(self) -> DoorKeyPublication: ...

    def relay_invite_registrations(self) -> list[InviteRegistration]: ...

    def relay_handle_bundle(self, bundle: SealedBundle) -> PickupAck:
        """Open, verify, enroll. Must not raise; returns the ack to send."""
        ...


@dataclass
class RelayStats:
    polls_ok: int = 0
    polls_failed: int = 0
    bundles_collected: int = 0
    bundles_enrolled: int = 0
    bundles_rejected: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    last_success_at: str | None = None
    resyncs: int = 0
    _degraded: bool = field(default=False, repr=False)

    @property
    def degraded(self) -> bool:
        return self._degraded


class RelayWorker:
    """Polls the relay for sealed bundles and hands them to the service."""

    def __init__(
        self,
        *,
        transport: RelayTransport,
        handler: RelayHandler,
        poll_interval_s: float,
        backoff_max_s: float,
        resync_interval_s: float = 300.0,
    ) -> None:
        self._transport = transport
        self._handler = handler
        self._poll_interval = poll_interval_s
        self._backoff_max = backoff_max_s
        self._resync_interval = resync_interval_s
        self._stats = RelayStats()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._needs_resync = True
        self._since_resync = 0.0

    @property
    def stats(self) -> RelayStats:
        return self._stats

    def request_resync(self) -> None:
        """Republish the door key and open invites on the next tick (e.g. after rotation)."""
        self._needs_resync = True

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="visiond-relay-worker")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        logger.info("relay_worker_started")
        while self._running:
            delay = self._poll_interval
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                # A relay problem must never kill the loop or reach the door path.
                delay = self._register_failure(exc)
            await asyncio.sleep(delay)

    async def _tick(self) -> None:
        if self._needs_resync or self._since_resync >= self._resync_interval:
            await self._resync()

        if not self._handler.relay_collection_allowed():
            # Pending bundles stay in the relay and expire on their own TTL; we
            # produce no plaintext while recognition is off or storage is locked.
            self._since_resync += self._poll_interval
            return

        batch = await asyncio.to_thread(self._transport.poll_pickup)
        self._register_success()
        self._since_resync += self._poll_interval

        for item in batch.items:
            self._stats.bundles_collected += 1
            ack = self._handler.relay_handle_bundle(item.bundle)
            if ack.outcome == "enrolled":
                self._stats.bundles_enrolled += 1
            else:
                self._stats.bundles_rejected += 1
            await asyncio.to_thread(self._transport.acknowledge, ack)
            logger.info(
                "relay_bundle_acknowledged",
                extra={"bundle_id": ack.bundle_id, "outcome": ack.outcome, "reason": ack.reason},
            )

    async def _resync(self) -> None:
        """Republish the door key and open invites. Idempotent, safe to repeat."""
        await asyncio.to_thread(
            self._transport.publish_door_key, self._handler.relay_door_key_publication()
        )
        for registration in self._handler.relay_invite_registrations():
            await asyncio.to_thread(self._transport.register_invite, registration)
        self._needs_resync = False
        self._since_resync = 0.0
        self._stats.resyncs += 1
        logger.info("relay_resynced")

    def _register_success(self) -> None:
        self._stats.polls_ok += 1
        self._stats.consecutive_failures = 0
        self._stats.last_error = None
        self._stats._degraded = False
        self._stats.last_success_at = datetime.now(UTC).isoformat()

    def _register_failure(self, exc: Exception) -> float:
        self._stats.polls_failed += 1
        self._stats.consecutive_failures += 1
        self._stats.last_error = type(exc).__name__
        self._stats._degraded = True
        # Whatever went wrong, assume relay-side state may be missing and
        # republish on the next successful tick.
        self._needs_resync = True
        delay = min(
            self._poll_interval * (2 ** min(self._stats.consecutive_failures, 8)),
            self._backoff_max,
        )
        logger.warning(
            "relay_poll_failed",
            extra={
                "error_class": type(exc).__name__,
                "consecutive": self._stats.consecutive_failures,
                "retry_in_s": delay,
            },
        )
        return delay
