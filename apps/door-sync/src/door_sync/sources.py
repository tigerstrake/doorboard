"""door-media event source — the real-time enqueue path.

Subscribes to door-media's ``GET /events`` (SSE) and enqueues archive work as
recordings finalize (``media.recording_finalized`` → clip,
``media.thumbnail_ready`` → thumbnail). This is the fast path; startup
reconciliation (``SyncEngine.reconcile_from_media``) is the backstop that
guarantees nothing is lost if this stream was down when an event fired.

The consumer reconnects forever with bounded backoff — a door that runs for
months must survive door-media restarts without operator help — and every
enqueue is idempotent, so a reconnect that replays nothing new is harmless.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from door_sync.engine import SyncEngine

logger = logging.getLogger("door_sync.sources")


class MediaEventSource:
    def __init__(
        self,
        engine: SyncEngine,
        *,
        base_url: str,
        reconnect_min_s: float = 1.0,
        reconnect_max_s: float = 30.0,
    ) -> None:
        self._engine = engine
        self._base_url = base_url.rstrip("/")
        self._reconnect_min_s = reconnect_min_s
        self._reconnect_max_s = reconnect_max_s
        self._running = False

    def handle_event(self, event: dict) -> None:
        """Enqueue archive work for one door-media event. Unknown types ignored."""
        etype = event.get("type")
        payload = event.get("payload", {})
        trace_id = event.get("trace_id", "")
        if etype == "media.recording_finalized":
            self._engine.enqueue_recording(
                recording_id=payload["recording_id"],
                local_path=payload["path"],
                sha256=payload["sha256"],
                trace_id=trace_id,
            )
        elif etype == "media.thumbnail_ready":
            self._engine.enqueue_thumbnail(
                recording_id=payload["recording_id"],
                local_path=payload["path"],
                trace_id=trace_id,
            )

    async def run(self) -> None:
        self._running = True
        delay = self._reconnect_min_s
        while self._running:
            try:
                await self._consume_once()
                delay = self._reconnect_min_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("media_sse_disconnected", extra={"error": str(exc)[:200]})
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._reconnect_max_s)

    async def _consume_once(self) -> None:
        async with (
            httpx.AsyncClient(timeout=None) as client,
            client.stream("GET", f"{self._base_url}/events") as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not self._running:
                    return
                if not line.startswith("data:"):
                    continue
                raw = line[len("data:") :].strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                    self.handle_event(event)
                except (json.JSONDecodeError, KeyError) as exc:
                    # One malformed frame must never kill the stream.
                    logger.warning("media_sse_bad_frame", extra={"error": str(exc)[:200]})

    def stop(self) -> None:
        self._running = False
