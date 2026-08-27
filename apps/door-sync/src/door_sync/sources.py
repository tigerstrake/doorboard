"""door-media event source — the real-time enqueue path.

Subscribes to door-media's ``GET /events`` (SSE) and, per media event, does two
idempotent things: enqueues NAS archive work as recordings finalize
(``media.recording_finalized`` → clip, ``media.thumbnail_ready`` → thumbnail)
*and* mirrors the media metadata event itself to the NUC control plane
(``media.recording_started`` / ``_finalized`` / ``_thumbnail_ready`` →
``enqueue_event``) so its ``media_mirror`` read model — the table Telegram
video-message delivery reads — is populated. This is the fast path;
reconciliation (``SyncEngine.reconcile_from_media``) is the backstop that
guarantees nothing is lost when the stream was down as an event fired.

The consumer reconnects forever with bounded backoff — a door that runs for
months must survive door-media restarts without operator help — and every
enqueue is idempotent, so a reconnect that replays nothing new is harmless.
Because SSE has no replay, the backstop runs not just at startup but on every
*re*-connection (``_on_connected``): a door-media restart drops any events it
finalized while we were disconnected, and the reconcile after reconnect is what
recovers them.
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
        admin_token: str = "",
        reconnect_min_s: float = 1.0,
        reconnect_max_s: float = 30.0,
    ) -> None:
        self._engine = engine
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {admin_token}"} if admin_token else {}
        self._reconnect_min_s = reconnect_min_s
        self._reconnect_max_s = reconnect_max_s
        self._running = False
        self._connected_once = False

    def handle_event(self, event: dict) -> None:
        """Handle one door-media event. Unknown types ignored.

        Two independent, idempotent effects per media event:

          - **NAS archive** — finalized clips/thumbnails become upload work so a
            durable copy is kept off the door (``enqueue_recording`` /
            ``enqueue_thumbnail``).
          - **NUC mirror** — the *metadata* event itself is forwarded to the
            control plane (``enqueue_event``) so its ``media_mirror`` read model
            is populated. Without this the NUC never learns a recording exists,
            and Telegram video-message delivery logs ``telegram_video_no_recording``
            and never sends. door-api's ``session.*`` events already reach the NUC
            this same way; ``media.*`` events must too.

        Both dedupe (the NAS queue by ``recording_id``, ``enqueue_event`` by
        ``event_id``), so a reconnect that replays events is harmless.
        """
        etype = event.get("type")
        payload = event.get("payload", {})
        trace_id = event.get("trace_id", "")
        if etype == "media.recording_started":
            # Metadata only — no NAS artifact exists yet; mirror to the NUC so it
            # learns the recording's session_id/kind before finalize arrives.
            self._engine.enqueue_event(event)
        elif etype == "media.recording_finalized":
            self._engine.enqueue_recording(
                recording_id=payload["recording_id"],
                local_path=payload["path"],
                sha256=payload["sha256"],
                trace_id=trace_id,
            )
            self._engine.enqueue_event(event)
        elif etype == "media.thumbnail_ready":
            self._engine.enqueue_thumbnail(
                recording_id=payload["recording_id"],
                local_path=payload["path"],
                trace_id=trace_id,
            )
            self._engine.enqueue_event(event)

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

    async def _on_connected(self) -> None:
        """Run once the ``/events`` stream is open, to close the reconnect gap.

        SSE has no replay: anything door-media finalized while we were disconnected is never
        re-sent, so a door-media restart would silently drop those events. The startup
        reconcile (in the lifespan) covers the *first* connection; every reconnection after
        that runs the reconcile again to catch up. Reconcile is idempotent, so replaying
        already-seen work is harmless; a reconcile failure is logged and the stream proceeds
        (a later reconnect, or the next reconcile, catches up) rather than tearing the stream
        down.
        """
        if self._connected_once:
            try:
                await self._engine.reconcile_from_media()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "media_reconcile_after_reconnect_failed", extra={"error": str(exc)[:200]}
                )
        self._connected_once = True

    async def _consume_once(self) -> None:
        async with (
            httpx.AsyncClient(timeout=None) as client,
            client.stream("GET", f"{self._base_url}/events", headers=self._headers) as resp,
        ):
            resp.raise_for_status()
            await self._on_connected()
            async for line in resp.aiter_lines():
                if not self._running:
                    return
                if not line.startswith("data:"):
                    continue
                raw = line[len("data:") :].strip()
                if not raw:
                    continue
                self._handle_frame(raw)

    def _handle_frame(self, raw: str) -> None:
        """Process one SSE frame. A bad frame is logged and dropped, never raised.

        This is synchronous and swallows every error on purpose. The old inline catch
        was JSONDecodeError/KeyError only, but handle_event -> parse_event raises a
        pydantic ValidationError for a schema-invalid event — which escaped to run()'s
        reconnect path and tore the whole clip-sync stream down, mislogged as
        `media_sse_disconnected`. A single bad frame must never kill the stream; only a
        genuine transport failure (raised from _consume_once, above) triggers a reconnect.
        """
        try:
            event = json.loads(raw)
            self.handle_event(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning("media_sse_bad_frame", extra={"error": str(exc)[:200]})

    def stop(self) -> None:
        self._running = False
