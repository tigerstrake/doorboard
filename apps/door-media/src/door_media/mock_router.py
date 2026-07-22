"""Mock MediaRouter implementation — the CI / laptop path.

This router never touches a camera, ffmpeg, or MediaMTX.  It simulates the
recording lifecycle in memory so that:
  - All contract events are still emitted correctly.
  - The retention engine can exercise its limits.
  - Tests run on any laptop with no hardware.

The mock generates synthetic MP4 blobs of the requested duration by writing
random bytes (which is a valid exercise of the sha256/size paths).

Stream "health" always reports ``stream_up=True`` in mock mode.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from door_media._uuid7 import uuid7
from door_media.adapters import (
    CapturedPhoto,
    ConsentContext,
    FinalizedRecording,
    MediaStorageStatus,
    RecordingHandle,
    RecordingKind,
    StreamInfo,
)

if TYPE_CHECKING:
    from door_media.settings import Settings

logger = logging.getLogger("door_media.mock_router")

# Simulated bitrate for mock recordings (bytes/s at ~720p H.264 equivalent)
_MOCK_BYTES_PER_SECOND = 512_000


@dataclass
class _ActiveMock:
    handle: RecordingHandle
    started_wall: float = field(default_factory=time.monotonic)


class MockMediaRouter:
    """In-process mock that satisfies the MediaRouter protocol.

    All writes go under ``settings.recordings_root / "mock"`` on the real
    (test-temp) directory.  If that directory doesn't exist it is created.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._active: dict[UUID, _ActiveMock] = {}
        self._recordings_dir = settings.recordings_root / "mock"
        self._review_dir = settings.ssd_data_root / "photo-review"
        self._recordings_dir.mkdir(parents=True, exist_ok=True)
        self._review_dir.mkdir(parents=True, exist_ok=True)
        logger.info("mock_router_init", extra={"recordings_dir": str(self._recordings_dir)})

    # ------------------------------------------------------------------
    # MediaRouter protocol
    # ------------------------------------------------------------------

    async def start_recording(
        self,
        *,
        session_id: UUID,
        kind: RecordingKind,
        stream: str,
    ) -> RecordingHandle:
        """Immediately return a handle — no cold start."""
        recording_id = uuid7()
        handle = RecordingHandle(
            recording_id=recording_id,
            session_id=session_id,
            kind=kind,
            stream=stream,
            started_monotonic_ms=time.monotonic_ns() // 1_000_000,
        )
        self._active[recording_id] = _ActiveMock(handle=handle)
        logger.info(
            "mock_recording_started",
            extra={
                "recording_id": str(recording_id),
                "session_id": str(session_id),
                "kind": kind,
            },
        )
        return handle

    async def finalize_recording(
        self,
        handle: RecordingHandle,
        *,
        consent_context: ConsentContext,
    ) -> FinalizedRecording:
        """Write a synthetic file proportional to elapsed time and finalize."""
        active = self._active.pop(handle.recording_id, None)
        elapsed_s = (time.monotonic() - active.started_wall) if active else 1.0
        elapsed_s = max(elapsed_s, 0.1)  # floor to avoid zero-byte files

        # Generate synthetic bytes to simulate the recording
        byte_count = int(elapsed_s * _MOCK_BYTES_PER_SECOND)
        # Cap at 10 MB in mock mode to keep tests fast
        byte_count = min(byte_count, 10 * 1024 * 1024)

        out_path = self._recordings_dir / f"{handle.kind}_{handle.recording_id}.mp4"

        # Write deterministic (but non-zero) synthetic bytes
        rng = os.urandom(min(byte_count, 65536))
        sha = hashlib.sha256()
        with out_path.open("wb") as fh:
            remaining = byte_count
            while remaining > 0:
                chunk = rng[: min(remaining, 65536)]
                fh.write(chunk)
                sha.update(chunk)
                remaining -= len(chunk)

        size_bytes = out_path.stat().st_size
        digest = sha.hexdigest()

        # Relative path for storage in DB / events (SSD-relative)
        rel_path = str(out_path.relative_to(self._settings.ssd_data_root))

        logger.info(
            "mock_recording_finalized",
            extra={
                "recording_id": str(handle.recording_id),
                "path": rel_path,
                "duration_s": elapsed_s,
                "size_bytes": size_bytes,
                "sha256": digest,
            },
        )
        return FinalizedRecording(
            recording_id=handle.recording_id,
            path=rel_path,
            duration_s=elapsed_s,
            size_bytes=size_bytes,
            sha256=digest,
            consent_context=consent_context,
        )

    async def discard_recording(self, handle: RecordingHandle) -> None:
        """Drop a mock active recording without writing a file."""
        self._active.pop(handle.recording_id, None)
        logger.info(
            "mock_recording_discarded",
            extra={"recording_id": str(handle.recording_id)},
        )

    async def capture_photo(
        self,
        *,
        session_id: UUID,
        stream: str,
    ) -> CapturedPhoto:
        recording_id = uuid7()
        out_path = self._review_dir / f"photo_booth_{recording_id}.jpg"
        _write_mock_photo(out_path, recording_id=recording_id, session_id=session_id, stream=stream)
        digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
        return CapturedPhoto(
            recording_id=recording_id,
            session_id=session_id,
            path=str(out_path.relative_to(self._settings.ssd_data_root)),
            size_bytes=out_path.stat().st_size,
            sha256=digest,
            captured_monotonic_ms=time.monotonic_ns() // 1_000_000,
        )

    async def snapshot(self) -> bytes | None:
        """No live camera in mock mode — the app returns its placeholder JPEG."""
        return None

    def storage_status(self) -> MediaStorageStatus:
        """Return storage status based on the mock recordings directory."""
        recordings_dir = self._settings.ssd_data_root
        try:
            usage = _dir_size_bytes(recordings_dir)
            # Simulate plenty of free space in mock mode
            free_bytes = max(
                self._settings.min_free_bytes * 10 - usage,
                self._settings.min_free_bytes * 5,
            )
        except OSError:
            free_bytes = self._settings.min_free_bytes * 10
            usage = 0

        recording_allowed = free_bytes > self._settings.min_free_bytes
        return MediaStorageStatus(
            free_bytes=free_bytes,
            queue_depth=0,  # filled in by RecordingService from DB
            oldest_unsynced_s=0,
            recording_allowed=recording_allowed,
        )

    # ------------------------------------------------------------------
    # Stream info (always "up" in mock mode)
    # ------------------------------------------------------------------

    def stream_info(self) -> list[StreamInfo]:
        return [
            StreamInfo(
                name=self._settings.visitor_cam_stream,
                whep_url=f"http://127.0.0.1:8082/mock/whep/{self._settings.visitor_cam_stream}",
                stream_up=True,
                webrtc_clients=0,
            )
        ]

    async def health_check(self) -> bool:
        """Always healthy in mock mode."""
        return True


def _dir_size_bytes(path: Path) -> int:
    """Recursively sum file sizes under path (ignores errors)."""
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def _write_mock_photo(path: Path, *, recording_id: UUID, session_id: UUID, stream: str) -> None:
    """Write a tiny deterministic JPEG-like fixture for CI review flows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00\x48\x00\x48\x00\x00"
        + f"doorboard-photo:{recording_id}:{session_id}:{stream}".encode()
        + b"\xff\xd9"
    )
    path.write_bytes(payload)
