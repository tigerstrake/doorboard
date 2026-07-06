"""SyncEngine — orchestrates the durable drain of the upload queue.

Correctness contract (T-502 / ADR-0007), all proven in tests:

  - **Never lose a clip.** Enqueue is one committed transaction; startup
    reconciliation re-derives any finalized-but-unsynced clip door-media still
    holds, so a missed real-time event cannot strand a recording.
  - **Never delete unverified.** A clip's local copy is licensed for deletion
    (``POST /internal/sync_completed``) only after the archive target confirms a
    checksum-verified copy. Ordering: verify → mark ``completed`` → notify
    door-media → mark ``licensed`` → emit. A crash at any step re-drives from the
    last committed state without ever notifying before verification.
  - **Never duplicate.** Media re-uploads overwrite a deterministic
    ``dest_key``; NUC events dedupe by ``event_id``; purge is person-keyed. Retry
    after a crash is therefore idempotent on the far side.
  - **Bounded under long outages.** Transient (target-down) failures retry
    forever within bounded, jittered backoff and never dead-letter, so a
    multi-day NAS outage drains completely on recovery. Only permanent failures
    (4xx, checksum mismatch, missing file) accrue toward the cap and dead-letter.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from doorboard_contracts.events import BaseEvent, DoorboardEvent, parse_event

from door_sync import emitter
from door_sync.fence import resolve_syncable
from door_sync.media_client import MediaClient
from door_sync.queue import NewItem, QueueItem, UploadQueue
from door_sync.settings import Settings
from door_sync.targets import (
    MediaTarget,
    NucTarget,
    PermanentError,
    TransientError,
    sha256_file,
)

logger = logging.getLogger("door_sync.engine")


def _thumb_item_id(recording_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"doorboard-thumb:{recording_id}"))


def _purge_item_id(person_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"doorboard-purge:{person_id}"))


class SyncEngine:
    def __init__(
        self,
        *,
        queue: UploadQueue,
        settings: Settings,
        media_target: MediaTarget,
        nuc_target: NucTarget,
        media_client: MediaClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._queue = queue
        self._settings = settings
        self._media = media_target
        self._nuc = nuc_target
        self._media_client = media_client
        self._clock = clock
        self._running = False

    # ------------------------------------------------------------------
    # Clock helpers
    # ------------------------------------------------------------------

    def _now_epoch(self) -> float:
        return self._clock()

    def _backoff_delay(self, prior_attempts: int) -> float:
        capped = min(
            self._settings.backoff_base_s * (2**prior_attempts),
            self._settings.backoff_max_s,
        )
        # Equal jitter: half fixed, half random — bounded, never a busy 0-delay.
        return random.uniform(capped / 2.0, capped)  # noqa: S311 (not cryptographic)

    # ------------------------------------------------------------------
    # Enqueue API
    # ------------------------------------------------------------------

    def enqueue_recording(
        self, *, recording_id: str, local_path: str, sha256: str, trace_id: str
    ) -> bool:
        """Enqueue a finalized clip for archive. Path must pass the biometric fence."""
        resolve_syncable(
            local_path,
            ssd_data_root=self._settings.ssd_data_root,
            syncable_roots=self._settings.syncable_roots,
        )
        newly = self._queue.enqueue(
            NewItem(
                item_id=str(recording_id),
                kind="clip",
                target="nas",
                dest_key=local_path,
                trace_id=str(trace_id),
                recording_id=str(recording_id),
                local_path=local_path,
                expected_sha256=sha256,
            )
        )
        if newly:
            emitter.emit_upload_queued(
                item_id=UUID(str(recording_id)),
                recording_id=UUID(str(recording_id)),
                target="nas",
                trace_id=UUID(str(trace_id)),
                door_id=self._settings.door_id,
            )
        return newly

    def enqueue_thumbnail(self, *, recording_id: str, local_path: str, trace_id: str) -> bool:
        """Enqueue a thumbnail for archive (verified read-back — no declared sha)."""
        resolve_syncable(
            local_path,
            ssd_data_root=self._settings.ssd_data_root,
            syncable_roots=self._settings.syncable_roots,
        )
        item_id = _thumb_item_id(str(recording_id))
        newly = self._queue.enqueue(
            NewItem(
                item_id=item_id,
                kind="thumbnail",
                target="nas",
                dest_key=local_path,
                trace_id=str(trace_id),
                recording_id=str(recording_id),
                local_path=local_path,
                expected_sha256=None,
            )
        )
        if newly:
            emitter.emit_upload_queued(
                item_id=UUID(item_id),
                recording_id=UUID(str(recording_id)),
                target="nas",
                trace_id=UUID(str(trace_id)),
                door_id=self._settings.door_id,
            )
        return newly

    def enqueue_event(self, event: DoorboardEvent | dict) -> bool:
        """Mirror a contract event to the NUC. Validated now so a malformed event
        is rejected at the door rather than dead-lettered later."""
        parsed = event if isinstance(event, BaseEvent) else parse_event(event)
        event_json = parsed.model_dump_json()
        return self._queue.enqueue(
            NewItem(
                item_id=str(parsed.event_id),
                kind="event",
                target="nuc",
                dest_key=str(parsed.event_id),
                trace_id=str(parsed.trace_id),
                payload=event_json,
            )
        )

    def enqueue_purge(self, *, person_id: str, trace_id: str) -> bool:
        """Durably forward the ADR-0009 person-purge to the NUC. Never blocks the door."""
        return self._queue.enqueue(
            NewItem(
                item_id=_purge_item_id(person_id),
                kind="purge",
                target="nuc",
                dest_key=person_id,
                trace_id=str(trace_id),
                payload=person_id,
            )
        )

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    async def process_item(self, item: QueueItem) -> str:
        """Attempt one item once. Returns the resulting status."""
        try:
            if item.kind in ("clip", "thumbnail"):
                return await self._process_media(item)
            if item.kind == "event":
                await self._nuc.ingest_event(item.payload or "")
                self._queue.mark_completed(item.item_id, verified_sha256=None, licensed=True)
                return "completed"
            if item.kind == "purge":
                await self._nuc.purge_person(item.payload or "")
                self._queue.mark_completed(item.item_id, verified_sha256=None, licensed=True)
                return "completed"
            msg = f"unknown item kind {item.kind!r}"
            raise PermanentError(msg)
        except TransientError as exc:
            return self._on_failure(item, exc, permanent=False)
        except PermanentError as exc:
            return self._on_failure(item, exc, permanent=True)
        except Exception as exc:  # noqa: BLE001
            # An unclassified error (bug, unexpected OSError) must not abort the
            # drain pass and starve every later item. Treat it as transient:
            # log, back off, retry. A media item that got this far is still
            # ``pending`` (the completion commit happens only on the success
            # path), so recording a failure cannot corrupt a completed item.
            logger.exception("upload_item_unexpected_error", extra={"item_id": item.item_id})
            return self._on_failure(item, exc, permanent=False)

    async def _process_media(self, item: QueueItem) -> str:
        assert item.local_path is not None  # noqa: S101 (invariant of media items)
        abs_path = resolve_syncable(
            item.local_path,
            ssd_data_root=self._settings.ssd_data_root,
            syncable_roots=self._settings.syncable_roots,
        )
        if not abs_path.exists():
            msg = f"local media missing: {item.local_path}"
            raise PermanentError(msg)

        local_sha = await _to_thread_sha(abs_path)
        if item.expected_sha256 is not None and local_sha != item.expected_sha256:
            # The finalized clip on disk doesn't match what door-media declared —
            # corrupt/truncated; retrying the same bytes cannot fix it.
            msg = (
                f"local checksum mismatch for {item.local_path}: "
                f"{local_sha} != {item.expected_sha256}"
            )
            raise PermanentError(msg)
        expected = item.expected_sha256 or local_sha

        verified = await self._media.upload_and_verify(
            local_path=abs_path, dest_key=item.dest_key, expected_sha256=expected
        )

        if item.kind == "clip":
            # Verified — but NOT yet licensed for deletion. finalize handles the
            # door-media callback so a crash before it re-drives idempotently.
            self._queue.mark_completed(item.item_id, verified_sha256=verified, licensed=False)
            await self._finalize_license(self._require(item.item_id))
        else:  # thumbnail — no deletion license, terminal on completion
            self._queue.mark_completed(item.item_id, verified_sha256=verified, licensed=True)
            self._emit_completed(item.item_id)
        return "completed"

    async def _finalize_license(self, item: QueueItem) -> None:
        """Tell door-media a clip's archive copy is verified (idempotent).

        Order: notify → mark licensed → emit. A crash before ``mark_licensed``
        leaves the clip in the awaiting-license set for recovery; door-media
        never deleted anything because it was never told to.
        """
        assert item.recording_id is not None and item.verified_sha256 is not None  # noqa: S101
        try:
            await self._media_client.notify_synced(
                recording_id=UUID(item.recording_id),
                verified_sha256=item.verified_sha256,
                item_id=UUID(item.item_id),
                attempts=item.attempts,
            )
        except Exception:  # noqa: BLE001
            # The archive upload is already verified and committed; only the
            # idempotent license callback remains. Any failure here (door-media
            # unreachable or an unexpected error) leaves the clip
            # completed-but-unlicensed for finalize_licenses to re-drive — it must
            # never propagate into process_item's failure path and flip an
            # already-completed item back to pending. Never blocks; never deletes.
            logger.warning("license_callback_deferred", extra={"item_id": item.item_id})
            return
        self._queue.mark_licensed(item.item_id)
        self._emit_completed(item.item_id)

    async def finalize_licenses(self) -> int:
        """Re-drive deletion-license callbacks for verified-but-unlicensed clips.

        Called at startup (crash recovery) and each drain pass. Idempotent.
        """
        done = 0
        for item in self._queue.items_awaiting_license():
            await self._finalize_license(item)
            refreshed = self._queue.get(item.item_id)
            if refreshed is not None and refreshed.licensed:
                done += 1
        return done

    def _emit_completed(self, item_id: str) -> None:
        item = self._queue.get(item_id)
        if item is None or item.verified_sha256 is None:
            return
        emitter.emit_upload_completed(
            item_id=UUID(item.item_id),
            verified_sha256=item.verified_sha256,
            attempts=item.attempts,
            trace_id=UUID(item.trace_id),
            door_id=self._settings.door_id,
        )

    def _on_failure(self, item: QueueItem, exc: Exception, *, permanent: bool) -> str:
        delay = self._backoff_delay(item.attempts)
        next_epoch = self._now_epoch() + delay
        next_dt = datetime.fromtimestamp(next_epoch, UTC)
        error_class = type(exc).__name__
        status = self._queue.record_failure(
            item.item_id,
            permanent=permanent,
            next_attempt_at=next_epoch,
            error_class=error_class,
            message=str(exc),
            max_permanent_attempts=self._settings.max_permanent_attempts,
        )
        log = logger.error if status == "dead_letter" else logger.warning
        log(
            "upload_attempt_failed",
            extra={
                "item_id": item.item_id,
                "kind": item.kind,
                "permanent": permanent,
                "status": status,
                "attempts": item.attempts + 1,
                "error": str(exc)[:200],
            },
        )
        if item.kind in ("clip", "thumbnail"):
            emitter.emit_upload_failed(
                item_id=UUID(item.item_id),
                attempts=item.attempts + 1,
                next_retry_at=next_dt,
                error_class=error_class,
                trace_id=UUID(item.trace_id),
                door_id=self._settings.door_id,
            )
        return status

    def _require(self, item_id: str) -> QueueItem:
        item = self._queue.get(item_id)
        if item is None:
            msg = f"queue item vanished: {item_id}"
            raise RuntimeError(msg)
        return item

    # ------------------------------------------------------------------
    # Reconciliation (safety net) + drain loop
    # ------------------------------------------------------------------

    async def reconcile_from_media(self) -> int:
        """Enqueue finalized-but-unsynced clips door-media still holds.

        The backstop for a missed real-time event. Best-effort: a door-media
        outage just means we try again next startup — nothing is lost.
        """
        try:
            pending = await self._media_client.list_pending_clips()
        except TransientError as exc:
            logger.warning("reconcile_skipped", extra={"error": str(exc)[:200]})
            return 0
        added = 0
        for rec in pending:
            path = rec.get("path")
            sha = rec.get("sha256")
            rid = rec.get("recording_id")
            if not path or not sha or not rid:
                continue
            try:
                if self.enqueue_recording(
                    recording_id=rid,
                    local_path=path,
                    sha256=sha,
                    trace_id=str(_uuid7_str()),
                ):
                    added += 1
            except Exception:  # fence violation or bad row — skip, never crash startup
                logger.warning("reconcile_item_skipped", extra={"recording_id": rid})
        return added

    async def run_once(self) -> int:
        now = self._now_epoch()
        processed = 0
        await self.finalize_licenses()
        for item in self._queue.due_items(now_epoch=now):
            await self.process_item(item)
            processed += 1
        return processed

    def prune(self) -> int:
        cutoff = datetime.fromtimestamp(
            self._now_epoch() - self._settings.completed_retention_s, UTC
        ).isoformat()
        return self._queue.prune_completed(older_than_iso=cutoff)

    async def run(self) -> None:
        self._running = True
        await self.finalize_licenses()
        last_prune = 0.0
        while self._running:
            try:
                await self.run_once()
                now = self._now_epoch()
                if now - last_prune > 3600:
                    self.prune()
                    last_prune = now
            except Exception:
                logger.exception("drain_loop_error")
            await _sleep(self._settings.poll_interval_s)

    def stop(self) -> None:
        self._running = False


def _uuid7_str() -> str:
    from door_sync._uuid7 import uuid7

    return str(uuid7())


async def _to_thread_sha(path: Path) -> str:
    import asyncio

    return await asyncio.to_thread(sha256_file, path)


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
