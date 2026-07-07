"""Upload target adapters — the only code that talks off-Pi.

Two capability surfaces, one per destination:

  - :class:`MediaTarget` archives finalized clips/thumbnails (NAS). It uploads
    bytes and then proves the far side holds them intact (checksum verify or
    verified read-back) before returning — that return is what licenses local
    deletion, so a target that cannot verify must raise, never return.
  - :class:`NucTarget` mirrors contract events and forwards the ADR-0009
    person-purge to control-plane-api (``/ingest`` + ``DELETE
    /people/{id}/events``), both idempotent on the far side.

Failures are classified so the engine knows whether to retry forever or give
up: :class:`TransientError` (the target is unreachable/5xx — retry within
backoff, never dead-letter) vs :class:`PermanentError` (the item itself is
unprocessable — 4xx, checksum mismatch, missing local file — dead-letter after
the cap).

The concrete NAS implementation here is filesystem-backed (a mounted share).
Real SFTP/rsync provisioning is deploy/nas scope; the filesystem adapter models
a mounted share exactly and is what CI/dev exercise.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx


class TransientError(Exception):
    """Target temporarily unreachable — retry within backoff, never dead-letter."""


class PermanentError(Exception):
    """Item is fundamentally unprocessable — dead-letter after the cap."""


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


@runtime_checkable
class MediaTarget(Protocol):
    async def upload_and_verify(
        self, *, local_path: Path, dest_key: str, expected_sha256: str
    ) -> str:
        """Archive ``local_path`` at ``dest_key`` and verify the far-side bytes.

        Returns the verified sha256 (== ``expected_sha256``). Idempotent by
        ``dest_key``: re-uploading overwrites in place, so a retry after a crash
        never produces a duplicate. Raises :class:`TransientError` /
        :class:`PermanentError`.
        """
        ...


@runtime_checkable
class NucTarget(Protocol):
    async def ingest_event(self, event_json: str) -> None: ...
    async def purge_person(self, person_id: str) -> None: ...


# ---------------------------------------------------------------------------
# Filesystem NAS target
# ---------------------------------------------------------------------------


class FilesystemNasTarget:
    """NAS archive on a mounted filesystem share.

    Writes atomically (temp file + fsync + rename) under ``nas_root`` and
    verifies by reading the destination back and hashing it. A missing/unwritable
    ``nas_root`` is treated as the mount being down (:class:`TransientError`).
    """

    def __init__(self, nas_root: Path) -> None:
        self._nas_root = nas_root

    async def upload_and_verify(
        self, *, local_path: Path, dest_key: str, expected_sha256: str
    ) -> str:
        import asyncio

        return await asyncio.to_thread(
            self._upload_and_verify_sync, local_path, dest_key, expected_sha256
        )

    def _upload_and_verify_sync(self, local_path: Path, dest_key: str, expected_sha256: str) -> str:
        if not local_path.exists():
            msg = f"local media missing: {local_path}"
            raise PermanentError(msg)
        # Mount presence check: the share root must exist and be a directory.
        if not self._nas_root.exists() or not self._nas_root.is_dir():
            msg = f"NAS share not mounted at {self._nas_root}"
            raise TransientError(msg)

        dest = self._nas_root / dest_key
        tmp = dest.with_name(dest.name + ".part")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with local_path.open("rb") as src, tmp.open("wb") as out:
                shutil.copyfileobj(src, out, length=1 << 20)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, dest)
        except FileNotFoundError as exc:
            # Local file vanished mid-copy — unprocessable.
            raise PermanentError(str(exc)) from exc
        except OSError as exc:
            # Write failed against the share — treat as the mount misbehaving.
            with_suppressed_unlink(tmp)
            raise TransientError(str(exc)) from exc

        # Verified read-back from the far side.
        actual = sha256_file(dest)
        if actual != expected_sha256:
            # Corruption that a blind retry would only reproduce.
            with_suppressed_unlink(dest)
            msg = f"far-side checksum mismatch for {dest_key}: {actual} != {expected_sha256}"
            raise PermanentError(msg)
        return actual


def with_suppressed_unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# HTTP NUC target (control-plane-api)
# ---------------------------------------------------------------------------


class HttpNucTarget:
    """Mirror events and forward person-purge to control-plane-api.

    Both endpoints are idempotent on the NUC (events dedupe by ``event_id``,
    purge is person-keyed and re-runnable), so a crash-driven retry never
    double-stores.
    """

    def __init__(
        self,
        base_url: str,
        *,
        ingest_token: str,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = ingest_token
        self._timeout = timeout_s
        # transport is an injection seam for tests (httpx.ASGITransport); prod
        # leaves it None and httpx uses the network.
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def ingest_event(self, event_json: str) -> None:
        import json as _json

        payload = {"batch_id": "door-sync", "events": [_json.loads(event_json)]}
        try:
            async with self._client() as client:
                resp = await client.post(
                    f"{self._base_url}/ingest", json=payload, headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise TransientError(f"ingest transport error: {exc}") from exc
        self._raise_for_status(resp)
        # A batch of one: inspect the single per-event result. A "rejected"
        # (schema-invalid) event will never succeed on retry → permanent.
        body = resp.json()
        results = body.get("results", [])
        if results and results[0].get("status") == "rejected":
            raise PermanentError(f"event rejected by NUC: {results[0].get('error')}")

    async def purge_person(self, person_id: str) -> None:
        try:
            async with self._client() as client:
                resp = await client.delete(
                    f"{self._base_url}/people/{person_id}/events", headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise TransientError(f"purge transport error: {exc}") from exc
        self._raise_for_status(resp)

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        # 401/403 (revoked/expired token) are transient from the door's view —
        # the NUC can re-issue; giving up would silently strand the queue.
        if resp.status_code >= 500 or resp.status_code in (401, 403, 408, 429):
            raise TransientError(f"NUC {resp.status_code}: {resp.text[:200]}")
        raise PermanentError(f"NUC {resp.status_code}: {resp.text[:200]}")


# ---------------------------------------------------------------------------
# Mock targets (dev without a share / NUC; failure injection for tests)
# ---------------------------------------------------------------------------


class MockMediaTarget:
    """In-memory media archive with injectable outage/corruption.

    ``down`` makes every upload raise :class:`TransientError` (models a NAS
    outage); ``corrupt`` makes verification fail (:class:`PermanentError`).
    Stores one entry per ``dest_key`` so re-upload is idempotent — the archive
    never grows a duplicate for the same key.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}  # dest_key -> verified sha256
        self.upload_calls: list[str] = []
        self.down = False
        self.corrupt = False

    async def upload_and_verify(
        self, *, local_path: Path, dest_key: str, expected_sha256: str
    ) -> str:
        self.upload_calls.append(dest_key)
        if self.down:
            raise TransientError("mock NAS down")
        if not local_path.exists():
            raise PermanentError(f"local media missing: {local_path}")
        actual = sha256_file(local_path)
        if self.corrupt or actual != expected_sha256:
            raise PermanentError(f"mock checksum mismatch for {dest_key}")
        self.store[dest_key] = actual
        return actual


class MockNucTarget:
    """In-memory NUC mirror with idempotent ``event_id``/person dedupe."""

    def __init__(self) -> None:
        self.events: dict[str, str] = {}  # event_id -> json
        self.purges: dict[str, int] = {}  # person_id -> call count
        self.down = False

    async def ingest_event(self, event_json: str) -> None:
        import json as _json

        if self.down:
            raise TransientError("mock NUC down")
        event = _json.loads(event_json)
        self.events.setdefault(event["event_id"], event_json)

    async def purge_person(self, person_id: str) -> None:
        if self.down:
            raise TransientError("mock NUC down")
        self.purges[person_id] = self.purges.get(person_id, 0) + 1
