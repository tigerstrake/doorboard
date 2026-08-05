"""Outbound visitor relay client (ADR-0017).

The wallboard QR has to work for a stranger standing at the door, whose phone is
on cellular and cannot resolve ``door.local``.  This module is how that works
without exposing door-api: it pushes a narrow snapshot of public session state to
the relay and collects queued visitor writes on an outbound poll.

Three properties this file is responsible for:

* **The snapshot is an allow-list, built field by field** (E-15).  It is never
  derived from the session machine's own object, because a future refactor
  upstream must not be able to start publishing identity or media to a public
  page as a side effect.  ADR-0017 §2 is the binding list;
  ``VISITOR_SNAPSHOT_FIELDS`` in contracts is that list as data.
* **Nothing here can delay the door.**  The session state machine never awaits
  this.  A hung relay costs a background task a timeout, and nothing else.
* **Content authority stays local** (E-18).  Collected writes go through the
  existing ``SocialService`` — the same sanitiser, the same rate limits as the LAN
  path.  This module translates and applies; it does not validate content itself.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import secrets
import string
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from doorboard_contracts.enrollment_relay import (
    VisitorActionOutcome,
    VisitorPoll,
    VisitorPollOption,
    VisitorPollResult,
    VisitorQueuedAction,
    VisitorSessionSnapshot,
)

logger = logging.getLogger("door_api.visitor_relay")

_BASE62 = string.digits + string.ascii_lowercase + string.ascii_uppercase


class VisitorRelayError(RuntimeError):
    """Any relay exchange that did not complete. Carries no visitor data."""


def sha256_b64url(value: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest()).decode().rstrip("=")


def opaque_session_id(session_id: str) -> str:
    """Map a session UUID to the relay's opaque-id format, deterministically.

    A UUID with dashes does not match the contracts' opaque-id pattern, and the
    relay has no business seeing our internal id shape either. Hashing keeps it
    stable across pushes for one session without leaking the original.

    Hex rather than base64url because the opaque-id pattern is strictly
    alphanumeric — base64url's ``-`` and ``_`` would fail validation.
    """
    digest = hashlib.sha256(f"doorboard-visitor-session|{session_id}".encode()).hexdigest()
    return "ses_" + digest[:22]


def new_action_id() -> str:
    return "act_" + "".join(secrets.choice(_BASE62) for _ in range(22))


class VisitorRelayTransport(Protocol):
    def push_snapshot(self, snapshot: VisitorSessionSnapshot) -> None: ...

    def poll_actions(self) -> list[VisitorQueuedAction]: ...

    def acknowledge(self, outcomes: list[dict[str, Any]]) -> None: ...


class HttpVisitorRelayTransport:
    """urllib transport, matching the pattern door-visiond's relay client uses."""

    def __init__(self, *, base_url: str, device_token: str, timeout_s: float) -> None:
        self._base = base_url.rstrip("/")
        self._token = device_token
        self._timeout = timeout_s

    def _request(self, method: str, path: str, payload: object | None = None) -> dict[str, Any]:
        url = f"{self._base}{path}"
        if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
            # Visitor content is not secret, but the device token is. Loopback is
            # allowed so a local relay can be developed against.
            msg = "relay base URL must be https (or loopback for local testing)"
            raise VisitorRelayError(msg)
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(  # noqa: S310 — scheme validated above
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._token}",
                **({"Content-Type": "application/json"} if body else {}),
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise VisitorRelayError(f"relay returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VisitorRelayError(f"relay unreachable: {type(exc).__name__}") from exc
        if not raw:
            return {}
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VisitorRelayError("relay returned malformed JSON") from exc
        return decoded if isinstance(decoded, dict) else {}

    def push_snapshot(self, snapshot: VisitorSessionSnapshot) -> None:
        self._request("PUT", "/api/visitor/session", snapshot.model_dump(mode="json"))

    def poll_actions(self) -> list[VisitorQueuedAction]:
        body = self._request("GET", "/api/visitor/pickup")
        items = body.get("items", [])
        if not isinstance(items, list):
            return []
        return [VisitorQueuedAction.model_validate(item) for item in items]

    def acknowledge(self, outcomes: list[dict[str, Any]]) -> None:
        self._request("POST", "/api/visitor/pickup/ack", {"outcomes": outcomes})


@dataclass
class VisitorRelayStats:
    pushes_ok: int = 0
    pushes_failed: int = 0
    polls_ok: int = 0
    polls_failed: int = 0
    actions_applied: int = 0
    actions_rejected: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    last_success_monotonic: float | None = field(default=None)

    @property
    def reachable(self) -> bool:
        return self.consecutive_failures == 0 and self.last_success_monotonic is not None


class VisitorRelayHandler(Protocol):
    """What the worker needs from door-api. Keeps session/social logic out of here."""

    def visitor_relay_snapshot(self) -> VisitorSessionSnapshot | None:
        """The current snapshot, or None when there is no live visitor session."""
        ...

    def visitor_relay_apply(self, action: VisitorQueuedAction) -> VisitorActionOutcome:
        """Apply one collected action. Must not raise."""
        ...


class VisitorRelayWorker:
    """Pushes session snapshots and collects visitor writes."""

    def __init__(
        self,
        *,
        transport: VisitorRelayTransport,
        handler: VisitorRelayHandler,
        poll_interval_s: float,
        backoff_max_s: float,
    ) -> None:
        self._transport = transport
        self._handler = handler
        self._poll_interval = poll_interval_s
        self._backoff_max = backoff_max_s
        self._stats = VisitorRelayStats()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._push_requested = asyncio.Event()

    @property
    def stats(self) -> VisitorRelayStats:
        return self._stats

    def request_push(self) -> None:
        """Ask for a snapshot push on the next tick (called on state transitions).

        Deliberately just a flag: the state machine's transition path must not block
        on, or fail because of, anything network-shaped.
        """
        self._push_requested.set()

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="door-api-visitor-relay")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        logger.info("visitor_relay_worker_started")
        while self._running:
            delay = self._poll_interval
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                delay = self._register_failure(exc)
            await asyncio.sleep(delay)

    async def _tick(self) -> None:
        snapshot = self._handler.visitor_relay_snapshot()
        if snapshot is not None:
            await asyncio.to_thread(self._transport.push_snapshot, snapshot)
            self._stats.pushes_ok += 1
            self._push_requested.clear()
        elif self._push_requested.is_set():
            # A transition asked for a push but the session has already gone; there
            # is nothing public left to publish, so drop the request.
            self._push_requested.clear()

        actions = await asyncio.to_thread(self._transport.poll_actions)
        self._register_success()

        if not actions:
            return

        outcomes: list[dict[str, Any]] = []
        for action in actions:
            outcome = self._handler.visitor_relay_apply(action)
            if outcome.status == "applied":
                self._stats.actions_applied += 1
            else:
                self._stats.actions_rejected += 1
            outcomes.append({**outcome.model_dump(mode="json"), "session_id": action.session_id})
            logger.info(
                "visitor_relay_action_applied",
                extra={
                    "action_id": outcome.action_id,
                    "kind": outcome.kind,
                    "status": outcome.status,
                    "reason": outcome.reason,
                },
            )
        await asyncio.to_thread(self._transport.acknowledge, outcomes)

    def _register_success(self) -> None:
        loop = asyncio.get_running_loop()
        self._stats.polls_ok += 1
        self._stats.consecutive_failures = 0
        self._stats.last_error = None
        self._stats.last_success_monotonic = loop.time()

    def _register_failure(self, exc: Exception) -> float:
        self._stats.polls_failed += 1
        self._stats.consecutive_failures += 1
        self._stats.last_error = type(exc).__name__
        delay = min(
            self._poll_interval * (2 ** min(self._stats.consecutive_failures, 8)),
            self._backoff_max,
        )
        logger.warning(
            "visitor_relay_poll_failed",
            extra={
                "error_class": type(exc).__name__,
                "consecutive": self._stats.consecutive_failures,
                "retry_in_s": delay,
            },
        )
        return delay


def build_snapshot(
    *,
    session_token: str,
    session_id: str,
    state: str,
    expires_at: datetime,
    poll: dict[str, Any] | None,
    poll_results: list[dict[str, Any]] | None,
) -> VisitorSessionSnapshot:
    """Project public session state into the ADR-0017 §2 allow-list.

    Every field is named explicitly. Adding one here is a deliberate, reviewable
    act — which is the whole point (E-15).
    """
    projected_poll: VisitorPoll | None = None
    if poll is not None:
        projected_poll = VisitorPoll(
            poll_id=str(poll["id"]),
            question=str(poll["question"]),
            options=[
                VisitorPollOption(option_id=str(option["id"]), label=str(option["label"]))
                for option in poll.get("options", [])
            ][:8],
        )

    projected_results: list[VisitorPollResult] | None = None
    if poll_results is not None:
        projected_results = [
            VisitorPollResult(option_id=str(row["option_id"]), votes=int(row.get("votes", 0)))
            for row in poll_results
        ][:8]

    return VisitorSessionSnapshot(
        session_token_sha256=sha256_b64url(session_token),
        session_id=opaque_session_id(session_id),
        state=state,
        expires_at=expires_at.astimezone(UTC),
        poll=projected_poll,
        poll_results=projected_results,
        outcomes=[],
        pushed_at=datetime.now(UTC),
    )
