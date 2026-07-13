"""RecordingService — core business logic for the door-media service.

Owns the recording lifecycle, retention engine, and storage monitoring.
The MediaRouter is injected and abstracted away.

Key invariants enforced here:
  - ``recording_allowed`` gate: any start_recording call when
    ``recording_allowed=False`` is rejected and logged.
  - Retention: size/age caps are enforced on a background task; deletion
    is only of synced-or-policy-expired items.  The caller (retention task)
    calls ``mark_deleted()`` after unlinking the file.
  - No synchronous NAS/NUC call ever sits in the visitor interaction path.
  - ``sync.upload_completed`` events update the DB sync status; only then
    can the retention engine delete a local file.

The service is a single-instance object wired into the FastAPI app via
``app.state``.  Tests instantiate it directly with a mock router and a
temp-dir settings.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import shutil
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from door_media._uuid7 import uuid7
from door_media.adapters import (
    CapturedPhoto,
    ConsentContext,
    FinalizedRecording,
    MediaRouter,
    RecordingKind,
)
from door_media.db import RecordingDB, RecordingRow
from door_media.emitter import (
    emit_recording_finalized,
    emit_recording_started,
    emit_retention_deleted,
    emit_storage_alert,
    emit_storage_status,
    emit_thumbnail_ready,
)
from door_media.settings import Settings

logger = logging.getLogger("door_media.service")

# Thresholds for storage alert severity
_WARN_FREE_RATIO = 0.10  # warn when <10% free
_CRIT_FREE_RATIO = 0.05  # critical when <5% free


@dataclass(frozen=True)
class PhotoReview:
    recording_id: UUID
    session_id: UUID
    review_path: str
    review_url_path: str
    size_bytes: int
    sha256: str


class RecordingService:
    """Coordinates recording lifecycle, retention, and event emission."""

    def __init__(
        self,
        *,
        router: MediaRouter,
        db: RecordingDB,
        settings: Settings,
    ) -> None:
        self._router = router
        self._db = db
        self._settings = settings
        self._active_handles: dict[UUID, object] = {}  # recording_id → handle
        self._pending_finalized: dict[UUID, FinalizedRecording] = {}
        self._review_photos: dict[UUID, CapturedPhoto] = {}
        self._retention_task: asyncio.Task[None] | None = None
        self._storage_task: asyncio.Task[None] | None = None
        self._trace_id = uuid7()  # service-level trace; per-session traces override

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start background maintenance tasks."""
        self._settings.recordings_root.mkdir(parents=True, exist_ok=True)
        self._settings.segments_root.mkdir(parents=True, exist_ok=True)
        self._settings.thumbnails_root.mkdir(parents=True, exist_ok=True)
        self._review_dir().mkdir(parents=True, exist_ok=True)
        self._cleanup_review_dir()

        self._retention_task = asyncio.create_task(self._retention_loop(), name="retention-loop")
        self._storage_task = asyncio.create_task(
            self._storage_status_loop(), name="storage-status-loop"
        )
        logger.info("recording_service_started")

    async def stop(self) -> None:
        """Cancel background tasks gracefully."""
        for task in [self._retention_task, self._storage_task]:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        logger.info("recording_service_stopped")

    # ------------------------------------------------------------------
    # Recording lifecycle
    # ------------------------------------------------------------------

    async def start_recording(
        self,
        *,
        session_id: UUID,
        kind: RecordingKind,
        trace_id: UUID,
    ) -> UUID | None:
        """Trigger a recording window for a session event.

        Returns the recording_id on success, or None if recording is not
        currently allowed (storage full / rate-limited).

        Never raises — any error is logged and None is returned so the
        session state machine keeps flowing.
        """
        if len(self._active_handles) >= self._settings.max_active_recordings:
            logger.warning(
                "recording_skipped_active_limit",
                extra={"active_recordings": len(self._active_handles)},
            )
            return None

        # Guard: check storage before starting
        status = self._router.storage_status()
        if not status.recording_allowed:
            logger.warning(
                "recording_skipped_storage_full",
                extra={"session_id": str(session_id), "free_bytes": status.free_bytes},
            )
            return None

        try:
            handle = await self._router.start_recording(
                session_id=session_id,
                kind=kind,
                stream=self._settings.visitor_cam_stream,
            )
        except Exception as exc:
            logger.exception("router_start_recording_error", exc_info=exc)
            return None

        # Re-check after the await so concurrent starts cannot grow the map
        # beyond the configured limit.
        if len(self._active_handles) >= self._settings.max_active_recordings:
            logger.warning(
                "recording_discarded_active_limit",
                extra={"active_recordings": len(self._active_handles)},
            )
            with contextlib.suppress(Exception):
                await self._router.discard_recording(handle)
            return None

        # Persist to DB
        try:
            self._db.insert_started(
                recording_id=handle.recording_id,
                session_id=session_id,
                kind=kind,
                stream=handle.stream,
                started_mono_ms=handle.started_monotonic_ms,
            )
        except Exception as exc:
            logger.exception("db_insert_started_error", exc_info=exc)
            with contextlib.suppress(Exception):
                await self._router.discard_recording(handle)
            return None

        self._active_handles[handle.recording_id] = handle

        # Emit event (contract-typed, broadcast to queue)
        emit_recording_started(
            recording_id=handle.recording_id,
            session_id=session_id,
            kind=kind,
            stream=handle.stream,
            trace_id=trace_id,
            door_id=self._settings.door_id,
        )

        logger.info(
            "recording_started",
            extra={
                "recording_id": str(handle.recording_id),
                "session_id": str(session_id),
                "kind": kind,
                "trace_id": str(trace_id),
            },
        )
        return handle.recording_id

    async def finalize_recording(
        self,
        recording_id: UUID,
        *,
        consent_context: ConsentContext,
        trace_id: UUID,
        thumbnail_stub: bool = True,
    ) -> bool:
        """Finalize a recording window.

        Steps:
          1. Call the router to remux/cut the clip.
          2. Persist finalized metadata to DB.
          3. Emit ``media.recording_finalized``.
          4. Emit a stub ``media.thumbnail_ready`` (T-203 fills implementation).

        Returns True on success.  Never raises.
        """
        handle = self._active_handles.get(recording_id)
        if handle is None:
            logger.warning(
                "finalize_unknown_recording",
                extra={"recording_id": str(recording_id)},
            )
            return False

        finalized = self._pending_finalized.get(recording_id)
        if finalized is None:
            try:
                finalized = await self._router.finalize_recording(
                    handle,  # type: ignore[arg-type]
                    consent_context=consent_context,
                )
            except Exception as exc:
                logger.exception("router_finalize_error", exc_info=exc)
                return False
            self._pending_finalized[recording_id] = finalized

        # Persist
        try:
            self._db.update_finalized(
                recording_id=finalized.recording_id,
                path=finalized.path,
                duration_s=finalized.duration_s,
                size_bytes=finalized.size_bytes,
                sha256=finalized.sha256,
                consent_context=finalized.consent_context,
            )
        except Exception as exc:
            logger.exception("db_update_finalized_error", exc_info=exc)
            return False

        self._pending_finalized.pop(recording_id, None)
        self._active_handles.pop(recording_id, None)

        # Emit finalized
        emit_recording_finalized(
            recording_id=finalized.recording_id,
            path=finalized.path,
            duration_s=finalized.duration_s,
            size_bytes=finalized.size_bytes,
            sha256=finalized.sha256,
            consent_context=finalized.consent_context,
            trace_id=trace_id,
            door_id=self._settings.door_id,
        )

        # Generate thumbnail (T-203 actual thumbnail generation)
        if thumbnail_stub:
            clip_abs = self._settings.ssd_data_root / finalized.path
            thumb_abs = clip_abs.with_suffix(".jpg")
            thumb_rel = thumb_abs.relative_to(self._settings.ssd_data_root)

            thumb_ok = await self._generate_thumbnail(
                clip_path=clip_abs,
                thumb_path=thumb_abs,
                duration_s=finalized.duration_s,
            )

            if thumb_ok:
                try:
                    self._db.update_thumbnail(
                        recording_id=recording_id,
                        thumbnail_path=str(thumb_rel),
                    )
                except Exception as exc:
                    logger.exception("db_update_thumbnail_error", exc_info=exc)
                emit_thumbnail_ready(
                    recording_id=recording_id,
                    path=str(thumb_rel),
                    trace_id=trace_id,
                    door_id=self._settings.door_id,
                )
            else:
                logger.warning(
                    "thumbnail_generation_failed",
                    extra={"recording_id": str(recording_id)},
                )

        logger.info(
            "recording_finalized",
            extra={
                "recording_id": str(recording_id),
                "path": finalized.path,
                "duration_s": finalized.duration_s,
                "size_bytes": finalized.size_bytes,
            },
        )
        return True

    # ------------------------------------------------------------------
    # Explicit photo-booth still capture
    # ------------------------------------------------------------------

    async def capture_photo_for_review(
        self,
        *,
        session_id: UUID,
        trace_id: UUID,
    ) -> PhotoReview | None:
        """Capture a still for local review without creating a durable artifact.

        The file lives under ``photo-review/`` and is not inserted in the
        recording registry until ``save_photo()``. Discarding a review capture
        therefore leaves no sync-visible rows and only has one temporary file to
        unlink.
        """
        self._prune_review_photos()
        status = self._router.storage_status()
        if not status.recording_allowed:
            logger.warning(
                "photo_capture_skipped_storage_full",
                extra={"session_id": str(session_id), "free_bytes": status.free_bytes},
            )
            return None

        try:
            captured = await self._router.capture_photo(
                session_id=session_id,
                stream=self._settings.visitor_cam_stream,
            )
        except Exception as exc:
            logger.exception("router_capture_photo_error", exc_info=exc)
            return None

        self._review_photos[captured.recording_id] = captured
        self._prune_review_photos()
        logger.info(
            "photo_review_captured",
            extra={
                "recording_id": str(captured.recording_id),
                "session_id": str(session_id),
                "trace_id": str(trace_id),
            },
        )
        return PhotoReview(
            recording_id=captured.recording_id,
            session_id=session_id,
            review_path=captured.path,
            review_url_path=f"/photos/{captured.recording_id}/review?session_id={session_id}",
            size_bytes=captured.size_bytes,
            sha256=captured.sha256,
        )

    async def save_photo(
        self,
        recording_id: UUID,
        *,
        session_id: UUID,
        trace_id: UUID,
    ) -> dict | None:
        """Promote a reviewed still into the media artifact registry."""
        captured = self._review_photos.pop(recording_id, None)
        if captured is None or captured.session_id != session_id:
            logger.warning(
                "save_unknown_photo_review",
                extra={"recording_id": str(recording_id), "session_id": str(session_id)},
            )
            return None

        src = self._settings.ssd_data_root / captured.path
        if not src.exists():
            logger.warning("save_photo_missing_review_file", extra={"path": captured.path})
            return None

        final_abs = self._settings.recordings_root / f"photo_booth_{recording_id}.jpg"
        final_abs.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), final_abs)
        final_rel = str(final_abs.relative_to(self._settings.ssd_data_root))
        sha256 = _sha256_file(final_abs)
        size_bytes = final_abs.stat().st_size

        thumb_abs = self._settings.thumbnails_root / f"photo_booth_{recording_id}.jpg"
        thumb_abs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(final_abs, thumb_abs)
        thumb_rel = str(thumb_abs.relative_to(self._settings.ssd_data_root))

        metadata_abs = self._settings.recordings_root / f"photo_booth_{recording_id}.consent.json"
        metadata = {
            "recording_id": str(recording_id),
            "session_id": str(session_id),
            "kind": "photo_booth",
            "consent_context": "visitor_initiated",
            "capture_mode": "explicit_photo_booth",
            "source_stream": self._settings.visitor_cam_stream,
            "captured_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "captured_monotonic_ms": captured.captured_monotonic_ms,
            "saved_after_review": True,
        }
        metadata_abs.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        metadata_rel = str(metadata_abs.relative_to(self._settings.ssd_data_root))

        try:
            self._db.insert_started(
                recording_id=recording_id,
                session_id=session_id,
                kind="photo_booth",
                stream=self._settings.visitor_cam_stream,
                started_mono_ms=captured.captured_monotonic_ms,
            )
            self._db.update_finalized(
                recording_id=recording_id,
                path=final_rel,
                duration_s=0.0,
                size_bytes=size_bytes,
                sha256=sha256,
                consent_context="visitor_initiated",
            )
            self._db.update_thumbnail(recording_id=recording_id, thumbnail_path=thumb_rel)
            self._db.update_consent_metadata(
                recording_id=recording_id,
                metadata_path=metadata_rel,
            )
        except Exception as exc:
            logger.exception("db_save_photo_error", exc_info=exc)

        emit_recording_started(
            recording_id=recording_id,
            session_id=session_id,
            kind="photo_booth",
            stream=self._settings.visitor_cam_stream,
            trace_id=trace_id,
            door_id=self._settings.door_id,
        )
        emit_recording_finalized(
            recording_id=recording_id,
            path=final_rel,
            duration_s=0.0,
            size_bytes=size_bytes,
            sha256=sha256,
            consent_context="visitor_initiated",
            trace_id=trace_id,
            door_id=self._settings.door_id,
        )
        emit_thumbnail_ready(
            recording_id=recording_id,
            path=thumb_rel,
            trace_id=trace_id,
            door_id=self._settings.door_id,
        )
        logger.info(
            "photo_saved",
            extra={"recording_id": str(recording_id), "path": final_rel},
        )
        return self._recording_dict(self._db.get(recording_id))

    async def discard_photo(self, recording_id: UUID, *, session_id: UUID) -> bool:
        """Discard a review photo. Saved photos use delete_recording()."""
        captured = self._review_photos.pop(recording_id, None)
        if captured is None or captured.session_id != session_id:
            return False
        with contextlib.suppress(OSError):
            (self._settings.ssd_data_root / captured.path).unlink(missing_ok=True)
        logger.info("photo_review_discarded", extra={"recording_id": str(recording_id)})
        return True

    def review_photo(self, recording_id: UUID, *, session_id: UUID) -> CapturedPhoto | None:
        """Return a pending review capture if it is still current."""
        self._prune_review_photos()
        captured = self._review_photos.get(recording_id)
        if captured is None or captured.session_id != session_id:
            return None
        return captured

    def _prune_review_photos(self) -> None:
        now_ms = time.monotonic_ns() // 1_000_000
        ttl_ms = max(self._settings.photo_review_ttl_s, 1) * 1000
        stale = [
            recording_id
            for recording_id, captured in self._review_photos.items()
            if now_ms - captured.captured_monotonic_ms > ttl_ms
        ]
        for recording_id in stale:
            self._discard_review_recording_id(recording_id, reason="expired")

        max_pending = max(self._settings.photo_review_max_pending, 1)
        overflow = len(self._review_photos) - max_pending
        if overflow <= 0:
            return
        oldest = sorted(
            self._review_photos.items(),
            key=lambda item: item[1].captured_monotonic_ms,
        )[:overflow]
        for recording_id, _captured in oldest:
            self._discard_review_recording_id(recording_id, reason="overflow")

    def _discard_review_recording_id(self, recording_id: UUID, *, reason: str) -> None:
        captured = self._review_photos.pop(recording_id, None)
        if captured is None:
            return
        with contextlib.suppress(OSError):
            (self._settings.ssd_data_root / captured.path).unlink(missing_ok=True)
        logger.info(
            "photo_review_pruned",
            extra={"recording_id": str(recording_id), "reason": reason},
        )

    def _cleanup_review_dir(self) -> None:
        for path in self._review_dir().glob("photo_booth_*"):
            with contextlib.suppress(OSError):
                path.unlink()

    def _review_dir(self) -> Path:
        return self._settings.ssd_data_root / "photo-review"

    async def _generate_thumbnail(
        self, clip_path: Path, thumb_path: Path, duration_s: float
    ) -> bool:
        """Generate a thumbnail for a clip using ffmpeg with an offset heuristic.

        Heuristic: Grab the frame at 1.0s, or half the duration if it is shorter than 2.0s.
        If duration is 0, grab the frame at 0.0s.
        """
        thumb_path.parent.mkdir(parents=True, exist_ok=True)

        if self._settings.media_mode == "mock":
            # In mock mode, if the file is empty/corrupted or missing, simulate failure
            if not clip_path.exists() or clip_path.stat().st_size == 0:
                logger.warning("mock_thumbnail_generation_failed_corrupted")
                return False
            # Otherwise, write the mock 1x1 JFIF stub
            try:
                _write_thumbnail_stub(thumb_path)
                return True
            except Exception as exc:
                logger.exception("mock_thumbnail_write_error", exc_info=exc)
                return False

        # Real mode (mediamtx) or generic fallback
        if not clip_path.exists() or clip_path.stat().st_size == 0:
            logger.warning(
                "thumbnail_skipped_input_missing_or_empty",
                extra={"path": str(clip_path)},
            )
            return False

        offset_s = min(1.0, duration_s / 2.0) if duration_s > 0 else 0.0
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-ss",
                f"{offset_s:.3f}",
                "-i",
                str(clip_path),
                "-vframes",
                "1",
                "-f",
                "image2",
                str(thumb_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(
                    "ffmpeg_thumbnail_failed",
                    extra={
                        "returncode": proc.returncode,
                        "stderr": stderr.decode(errors="replace")[:500],
                        "clip": str(clip_path),
                    },
                )
                return False
            return True
        except Exception as exc:
            logger.exception("thumbnail_generation_exception", exc_info=exc)
            return False

    # ------------------------------------------------------------------
    # Sync event handler
    # ------------------------------------------------------------------

    async def discard_recording(
        self,
        recording_id: UUID,
        *,
        trace_id: UUID,
    ) -> bool:
        """Discard an active or finalized recording and remove local files."""
        handle = self._active_handles.pop(recording_id, None)
        if handle is not None:
            try:
                await self._router.discard_recording(handle)  # type: ignore[arg-type]
            except Exception as exc:
                logger.exception("router_discard_error", exc_info=exc)
                return False
            self._db.delete_unfinalized(recording_id=recording_id)
            logger.info(
                "recording_discarded",
                extra={"recording_id": str(recording_id)},
            )
            return True
        return self._delete_recording(
            recording_id=recording_id,
            reason="user_request",
            trace_id=trace_id,
        )

    async def discard_recordings_for_session(
        self,
        *,
        session_id: UUID,
        kind: RecordingKind | None,
        trace_id: UUID,
    ) -> int:
        """Discard all active/finalized recordings for a session and kind."""
        discarded = 0
        active_ids = [
            rid
            for rid, handle in self._active_handles.items()
            if getattr(handle, "session_id", None) == session_id
            and (kind is None or getattr(handle, "kind", None) == kind)
        ]
        for rid in active_ids:
            if await self.discard_recording(rid, trace_id=trace_id):
                discarded += 1

        for row in self._db.rows_for_session(session_id=session_id, kind=kind):
            if row.sync_status != "deleted" and row.path is not None:
                if self._delete_recording(
                    recording_id=UUID(row.recording_id),
                    reason="user_request",
                    trace_id=trace_id,
                ):
                    discarded += 1
            elif row.path is None and self._db.delete_unfinalized(
                recording_id=UUID(row.recording_id)
            ):
                discarded += 1
        return discarded

    def on_sync_upload_completed(self, *, recording_id: UUID, verified_sha256: str) -> None:
        """Called when door-sync confirms a checksum-verified upload.

        This is the only gate that unlocks local deletion (ADR-0007).
        """
        matched = self._db.mark_synced(recording_id=recording_id, verified_sha256=verified_sha256)
        if matched:
            logger.info(
                "recording_marked_synced",
                extra={
                    "recording_id": str(recording_id),
                    "sha256": verified_sha256,
                },
            )

    def on_deletion_requested(self, *, recording_id: UUID, trace_id: UUID) -> bool:
        """Handle a ``social.deletion_requested`` for a video_message.

        Returns True if the recording was found and deleted.
        """
        return self._delete_recording(
            recording_id=recording_id,
            reason="user_request",
            trace_id=trace_id,
        )

    # ------------------------------------------------------------------
    # Admin API helpers
    # ------------------------------------------------------------------

    def list_recordings(
        self,
        *,
        kind: str | None = None,
        sync_status: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        """Return a list of recording metadata dicts for GET /recordings, along with next_cursor."""
        rows, next_cursor = self._db.list_recordings(
            kind=kind,
            sync_status=sync_status,
            limit=limit,
            cursor=cursor,
        )
        return [
            {
                "recording_id": r.recording_id,
                "session_id": r.session_id,
                "kind": r.kind,
                "stream": r.stream,
                "started_at_utc": r.started_at_utc,
                "finalized_at_utc": r.finalized_at_utc,
                "path": r.path,
                "duration_s": r.duration_s,
                "size_bytes": r.size_bytes,
                "sha256": r.sha256,
                "consent_context": r.consent_context,
                "thumbnail_path": r.thumbnail_path,
                "consent_metadata_path": r.consent_metadata_path,
                "sync_status": r.sync_status,
            }
            for r in rows
        ], next_cursor

    def delete_recording(self, recording_id: UUID, trace_id: UUID) -> bool:
        """Admin-initiated deletion (user_request reason)."""
        return self._delete_recording(
            recording_id=recording_id,
            reason="user_request",
            trace_id=trace_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _delete_recording(self, *, recording_id: UUID, reason: str, trace_id: UUID) -> bool:
        row = self._db.get(recording_id)
        if row is None:
            return False
        if row.sync_status == "deleted":
            return True  # already gone

        # Unlink the file
        if row.path:
            file_path = self._settings.ssd_data_root / row.path
            try:
                file_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.exception(
                    "delete_file_error",
                    exc_info=exc,
                    extra={"path": row.path},
                )
                return False

        # Unlink thumbnail
        if row.thumbnail_path:
            thumb_path = self._settings.ssd_data_root / row.thumbnail_path
            with contextlib.suppress(OSError):
                thumb_path.unlink(missing_ok=True)

        if row.consent_metadata_path:
            metadata_path = self._settings.ssd_data_root / row.consent_metadata_path
            with contextlib.suppress(OSError):
                metadata_path.unlink(missing_ok=True)

        self._db.mark_deleted(recording_id=recording_id, reason=reason)
        emit_retention_deleted(
            recording_id=recording_id,
            reason=reason,
            trace_id=trace_id,
            door_id=self._settings.door_id,
        )
        logger.info(
            "recording_deleted",
            extra={"recording_id": str(recording_id), "reason": reason},
        )
        return True

    def _recording_dict(self, row: RecordingRow | None) -> dict | None:
        if row is None:
            return None
        return {
            "recording_id": row.recording_id,
            "session_id": row.session_id,
            "kind": row.kind,
            "stream": row.stream,
            "started_at_utc": row.started_at_utc,
            "finalized_at_utc": row.finalized_at_utc,
            "path": row.path,
            "duration_s": row.duration_s,
            "size_bytes": row.size_bytes,
            "sha256": row.sha256,
            "consent_context": row.consent_context,
            "thumbnail_path": row.thumbnail_path,
            "consent_metadata_path": row.consent_metadata_path,
            "sync_status": row.sync_status,
        }

    # ------------------------------------------------------------------
    # Retention loop
    # ------------------------------------------------------------------

    async def _retention_loop(self) -> None:
        """Enforce size and age caps on recorded clips.

        Runs every 60 s.  On each pass:
          1. Check free space — if below min_free_bytes, stop new recordings
             and alert the control plane.
          2. Delete expired clips (age > max_clip_age_s, synced only).
          3. Delete oldest synced clips if over size cap.

        A clip is only deleted if it is synced.
        """
        logger.info("retention_loop_started")
        while True:
            try:
                await asyncio.sleep(60)
                await self._run_retention_pass()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("retention_loop_error", exc_info=exc)

    async def _run_retention_pass(self) -> None:
        trace_id = uuid7()
        root = self._settings.ssd_data_root

        # --- Storage check ---
        try:
            du = shutil.disk_usage(root)
            free_bytes = du.free
            total_bytes = du.total
        except OSError:
            # SSD not mounted — degrade gracefully
            logger.error("disk_usage_failed", extra={"root": str(root)})
            return

        recording_allowed = free_bytes > self._settings.min_free_bytes

        # Emit storage status
        pending = self._db.list_finalized_pending_sync()
        oldest_age = self._db.oldest_unsynced_age_s()
        emit_storage_status(
            free_bytes=free_bytes,
            queue_depth=len(pending),
            oldest_unsynced_s=oldest_age,
            recording_allowed=recording_allowed,
            trace_id=trace_id,
            door_id=self._settings.door_id,
        )

        # Emit storage alert if needed
        if total_bytes > 0:
            free_ratio = free_bytes / total_bytes
            if free_ratio < _CRIT_FREE_RATIO:
                emit_storage_alert(
                    host=socket.gethostname(),
                    mount=str(root),
                    free_bytes=free_bytes,
                    severity="critical",
                    trace_id=trace_id,
                    door_id=self._settings.door_id,
                )
            elif free_ratio < _WARN_FREE_RATIO:
                emit_storage_alert(
                    host=socket.gethostname(),
                    mount=str(root),
                    free_bytes=free_bytes,
                    severity="warning",
                    trace_id=trace_id,
                    door_id=self._settings.door_id,
                )

        # --- Age-based deletion (synced only) ---
        now_s = time.time()
        retention = self._settings.retention
        for row in self._db.list_pending():
            if row.sync_status != "synced":
                continue
            try:
                started = _parse_iso_epoch(row.started_at_utc)
                age_s = now_s - started
            except (ValueError, OSError):
                continue

            # Look up kind-specific age cap
            policy = getattr(retention, row.kind, None)
            max_age_s = policy.max_age_s if policy else self._settings.max_clip_age_s

            if age_s > max_age_s:
                self._delete_recording(
                    recording_id=UUID(row.recording_id),
                    reason="age",
                    trace_id=trace_id,
                )

        # --- Size-based deletion (synced only) ---
        # 1. Per-kind size caps
        for kind in ["bell_clip", "video_message", "photo_booth"]:
            policy = getattr(retention, kind, None)
            if not policy:
                continue

            kind_rows = [r for r in self._db.list_pending() if r.kind == kind]
            total_kind_size = sum(r.size_bytes or 0 for r in kind_rows)

            if total_kind_size > policy.max_size_bytes:
                logger.info(
                    "kind_size_cap_exceeded",
                    extra={
                        "kind": kind,
                        "total_size": total_kind_size,
                        "cap": policy.max_size_bytes,
                    },
                )
                for row in kind_rows:
                    if total_kind_size <= policy.max_size_bytes:
                        break
                    if row.sync_status == "synced":
                        self._delete_recording(
                            recording_id=UUID(row.recording_id),
                            reason="space",
                            trace_id=trace_id,
                        )
                        total_kind_size -= row.size_bytes or 0

        # 2. Global size cap
        all_rows = self._db.list_pending()
        total_size = sum(r.size_bytes or 0 for r in all_rows)
        if total_size > retention.max_recording_bytes:
            logger.info(
                "global_size_cap_exceeded",
                extra={
                    "total_size": total_size,
                    "cap": retention.max_recording_bytes,
                },
            )
            for row in all_rows:
                if total_size <= retention.max_recording_bytes:
                    break
                if row.sync_status == "synced":
                    self._delete_recording(
                        recording_id=UUID(row.recording_id),
                        reason="space",
                        trace_id=trace_id,
                    )
                    total_size -= row.size_bytes or 0

    # ------------------------------------------------------------------
    # Storage status broadcast
    # ------------------------------------------------------------------

    async def _storage_status_loop(self) -> None:
        """Periodically broadcast ``media.storage_status`` events."""
        logger.info("storage_status_loop_started")
        while True:
            try:
                await asyncio.sleep(self._settings.storage_status_interval_s)
                trace_id = uuid7()
                root = self._settings.ssd_data_root
                try:
                    free_bytes = shutil.disk_usage(root).free
                except OSError:
                    free_bytes = 0
                pending = self._db.list_finalized_pending_sync()
                oldest_age = self._db.oldest_unsynced_age_s()
                recording_allowed = free_bytes > self._settings.min_free_bytes
                emit_storage_status(
                    free_bytes=free_bytes,
                    queue_depth=len(pending),
                    oldest_unsynced_s=oldest_age,
                    recording_allowed=recording_allowed,
                    trace_id=trace_id,
                    door_id=self._settings.door_id,
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("storage_status_loop_error", exc_info=exc)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_iso_epoch(s: str) -> float:
    """Parse an ISO-8601 string to a Unix timestamp (float)."""
    from datetime import UTC, datetime

    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def _sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _write_thumbnail_stub(path: Path) -> None:
    """Write a minimal 1×1 JFIF stub so the path exists for T-203 to replace."""
    # Minimal 1x1 JFIF (JPEG) — valid enough for a stub
    _JFIF_1x1 = bytes(
        [
            0xFF,
            0xD8,
            0xFF,
            0xE0,
            0x00,
            0x10,
            0x4A,
            0x46,
            0x49,
            0x46,
            0x00,
            0x01,
            0x01,
            0x00,
            0x00,
            0x01,
            0x00,
            0x01,
            0x00,
            0x00,
            0xFF,
            0xDB,
            0x00,
            0x43,
            0x00,
            0x08,
            0x06,
            0x06,
            0x07,
            0x06,
            0x05,
            0x08,
            0x07,
            0x07,
            0x07,
            0x09,
            0x09,
            0x08,
            0x0A,
            0x0C,
            0x14,
            0x0D,
            0x0C,
            0x0B,
            0x0B,
            0x0C,
            0x19,
            0x12,
            0x13,
            0x0F,
            0x14,
            0x1D,
            0x1A,
            0x1F,
            0x1E,
            0x1D,
            0x1A,
            0x1C,
            0x1C,
            0x20,
            0x24,
            0x2E,
            0x27,
            0x20,
            0x22,
            0x2C,
            0x23,
            0x1C,
            0x1C,
            0x28,
            0x37,
            0x29,
            0x2C,
            0x30,
            0x31,
            0x34,
            0x34,
            0x34,
            0x1F,
            0x27,
            0x39,
            0x3D,
            0x38,
            0x32,
            0x3C,
            0x2E,
            0x33,
            0x34,
            0x32,
            0xFF,
            0xC0,
            0x00,
            0x0B,
            0x08,
            0x00,
            0x01,
            0x00,
            0x01,
            0x01,
            0x01,
            0x11,
            0x00,
            0xFF,
            0xC4,
            0x00,
            0x1F,
            0x00,
            0x00,
            0x01,
            0x05,
            0x01,
            0x01,
            0x01,
            0x01,
            0x01,
            0x01,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x01,
            0x02,
            0x03,
            0x04,
            0x05,
            0x06,
            0x07,
            0x08,
            0x09,
            0x0A,
            0x0B,
            0xFF,
            0xC4,
            0x00,
            0xB5,
            0x10,
            0x00,
            0x02,
            0x01,
            0x03,
            0x03,
            0x02,
            0x04,
            0x03,
            0x05,
            0x05,
            0x04,
            0x04,
            0x00,
            0x00,
            0x01,
            0x7D,
            0x01,
            0x02,
            0x03,
            0x00,
            0x04,
            0x11,
            0x05,
            0x12,
            0x21,
            0x31,
            0x41,
            0x06,
            0x13,
            0x51,
            0x61,
            0x07,
            0x22,
            0x71,
            0x14,
            0x32,
            0x81,
            0x91,
            0xA1,
            0x08,
            0x23,
            0x42,
            0xB1,
            0xC1,
            0x15,
            0x52,
            0xD1,
            0xF0,
            0x24,
            0x33,
            0x62,
            0x72,
            0x82,
            0x09,
            0x0A,
            0x16,
            0x17,
            0x18,
            0x19,
            0x1A,
            0x25,
            0x26,
            0x27,
            0x28,
            0x29,
            0x2A,
            0x34,
            0x35,
            0x36,
            0x37,
            0x38,
            0x39,
            0x3A,
            0x43,
            0x44,
            0x45,
            0x46,
            0x47,
            0x48,
            0x49,
            0x4A,
            0x53,
            0x54,
            0x55,
            0x56,
            0x57,
            0x58,
            0x59,
            0x5A,
            0x63,
            0x64,
            0x65,
            0x66,
            0x67,
            0x68,
            0x69,
            0x6A,
            0x73,
            0x74,
            0x75,
            0x76,
            0x77,
            0x78,
            0x79,
            0x7A,
            0x83,
            0x84,
            0x85,
            0x86,
            0x87,
            0x88,
            0x89,
            0x8A,
            0x92,
            0x93,
            0x94,
            0x95,
            0x96,
            0x97,
            0x98,
            0x99,
            0x9A,
            0xA2,
            0xA3,
            0xA4,
            0xA5,
            0xA6,
            0xA7,
            0xA8,
            0xA9,
            0xAA,
            0xB2,
            0xB3,
            0xB4,
            0xB5,
            0xB6,
            0xB7,
            0xB8,
            0xB9,
            0xBA,
            0xC2,
            0xC3,
            0xC4,
            0xC5,
            0xC6,
            0xC7,
            0xC8,
            0xC9,
            0xCA,
            0xD2,
            0xD3,
            0xD4,
            0xD5,
            0xD6,
            0xD7,
            0xD8,
            0xD9,
            0xDA,
            0xE1,
            0xE2,
            0xE3,
            0xE4,
            0xE5,
            0xE6,
            0xE7,
            0xE8,
            0xE9,
            0xEA,
            0xF1,
            0xF2,
            0xF3,
            0xF4,
            0xF5,
            0xF6,
            0xF7,
            0xF8,
            0xF9,
            0xFA,
            0xFF,
            0xDA,
            0x00,
            0x08,
            0x01,
            0x01,
            0x00,
            0x00,
            0x3F,
            0x00,
            0xFB,
            0x26,
            0x8A,
            0x28,
            0x03,
            0xFF,
            0xD9,
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_JFIF_1x1)
