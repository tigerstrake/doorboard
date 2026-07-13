"""Loopback client to door-media (same Pi).

Two jobs, both off the critical path:

  - **Reconcile.** On startup, list finalized-but-unsynced clips
    (``GET /recordings?sync_status=pending``) and enqueue any the queue is
    missing. This is the safety net that makes "never lose a clip" hold even if a
    real-time ``media.recording_finalized`` was missed (door-sync down when it
    fired). The SSE stream is the fast path; this is the backstop.
  - **License deletion.** After a checksum-verified archive upload, call
    ``POST /internal/sync_completed`` so door-media (which owns retention) may
    delete the local copy (ADR-0007). door-media's mark-synced is idempotent, so
    re-notifying after a crash is safe.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import httpx

from door_sync.targets import TransientError


class MediaClient(Protocol):
    async def list_pending_clips(self) -> list[dict]: ...
    async def notify_synced(
        self, *, recording_id: UUID, verified_sha256: str, item_id: UUID, attempts: int
    ) -> None: ...


class HttpMediaClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 10.0,
        admin_token: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._headers = {"Authorization": f"Bearer {admin_token}"} if admin_token else {}
        # Injection seam for tests (httpx.ASGITransport); None in prod.
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            headers=self._headers,
        )

    async def list_pending_clips(self) -> list[dict]:
        out: list[dict] = []
        cursor: str | None = None
        try:
            async with self._client() as client:
                while True:
                    params = {"sync_status": "pending", "limit": "200"}
                    if cursor:
                        params["cursor"] = cursor
                    resp = await client.get(f"{self._base_url}/recordings", params=params)
                    resp.raise_for_status()
                    body = resp.json()
                    out.extend(body.get("recordings", []))
                    cursor = body.get("next_cursor")
                    if not cursor:
                        break
        except httpx.HTTPError as exc:
            raise TransientError(f"door-media reconcile failed: {exc}") from exc
        return out

    async def notify_synced(
        self, *, recording_id: UUID, verified_sha256: str, item_id: UUID, attempts: int
    ) -> None:
        body = {
            "recording_id": str(recording_id),
            "verified_sha256": verified_sha256,
            "item_id": str(item_id),
            "attempts": attempts,
        }
        try:
            async with self._client() as client:
                resp = await client.post(f"{self._base_url}/internal/sync_completed", json=body)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TransientError(f"door-media license callback failed: {exc}") from exc
