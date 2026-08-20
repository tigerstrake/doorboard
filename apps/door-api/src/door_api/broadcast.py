"""WebSocket display broadcast — snapshot-on-connect + transition deltas.

Per the brief and api-conventions: new WebSocket clients receive a full
snapshot of the current session state on connect, then receive transition
deltas as they happen.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("door-api.broadcast")


class DisplayBroadcast:
    """Fan-out WebSocket broadcast for session state.

    Usage:
        broadcast = DisplayBroadcast()
        # In WebSocket handler:
        await broadcast.connect(websocket)
        # When session changes:
        broadcast.send_delta(event_dict)
    """

    def __init__(
        self,
        *,
        replay_source: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        # Events to hand a client right after its snapshot. A callable rather than stored
        # state, so this class keeps knowing nothing about ambient semantics — the policy
        # (which types, how stale is too stale) lives in door_api.ambient_cache.
        self._replay_source = replay_source
        self._clients: set[asyncio.Queue[str]] = set()
        self._last_snapshot: dict[str, Any] = {
            "session_id": None,
            "state": "IDLE",
            "person_id": None,
            "display_name": None,
            "profile_id": None,
        }

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def update_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Update the snapshot that new clients receive on connect."""
        self._last_snapshot = snapshot

    def send_delta(self, event: dict[str, Any]) -> None:
        """Broadcast a transition delta to all connected clients."""
        msg = json.dumps({"type": "delta", "event": event})
        stale: list[asyncio.Queue[str]] = []
        for queue in self._clients:
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                # Client is too slow — mark for removal.
                stale.append(queue)
        for q in stale:
            self._clients.discard(q)

    def make_client_queue(self) -> asyncio.Queue[str]:
        """Create a queue for a new client and enqueue the snapshot.

        The caller is responsible for calling ``remove_client`` when done.
        """
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        snapshot_msg = json.dumps({"type": "snapshot", "state": self._last_snapshot})
        try:
            queue.put_nowait(snapshot_msg)
        except asyncio.QueueFull:
            logger.warning("snapshot could not be enqueued — queue already full")

        # Then any remembered ambient events, in the ordinary delta envelope, so a client
        # that reconnects sees what a client listening all along would have. Replayed
        # *after* the snapshot: the snapshot is what the door needs to render at all, and a
        # full queue must drop the ambient nice-to-have rather than the session state.
        for event in self._replay_events():
            try:
                queue.put_nowait(json.dumps({"type": "delta", "event": event}))
            except asyncio.QueueFull:
                logger.warning("ambient replay truncated — client queue full")
                break

        self._clients.add(queue)
        return queue

    def _replay_events(self) -> list[dict[str, Any]]:
        """Ambient replay, best-effort: a client must connect even if this misbehaves."""
        if self._replay_source is None:
            return []
        try:
            return self._replay_source()
        except Exception:
            logger.warning("ambient replay source failed", exc_info=True)
            return []

    def remove_client(self, queue: asyncio.Queue[str]) -> None:
        """Remove a client queue from the broadcast set."""
        self._clients.discard(queue)
