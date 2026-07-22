"""door-media adapter interfaces and value objects.

``MediaRouter`` is the single abstraction over MediaMTX (hardware) and Mock
(CI/dev).  Implementations must satisfy this protocol — the service layer
only ever uses this interface.

``StreamInfo`` carries metadata about a live stream for the ``GET /streams``
endpoint consumed by kiosk UIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

RecordingKind = Literal["bell_clip", "video_message", "photo_booth"]
ConsentContext = Literal["visitor_initiated", "bell_event"]


@dataclass(frozen=True)
class RecordingHandle:
    recording_id: UUID
    session_id: UUID
    kind: RecordingKind
    stream: str
    started_monotonic_ms: int


@dataclass(frozen=True)
class FinalizedRecording:
    recording_id: UUID
    path: str
    duration_s: float
    size_bytes: int
    sha256: str
    consent_context: ConsentContext


@dataclass(frozen=True)
class CapturedPhoto:
    recording_id: UUID
    session_id: UUID
    path: str
    size_bytes: int
    sha256: str
    captured_monotonic_ms: int


@dataclass(frozen=True)
class MediaStorageStatus:
    free_bytes: int
    queue_depth: int
    oldest_unsynced_s: int
    recording_allowed: bool


@dataclass(frozen=True)
class StreamInfo:
    """Metadata about a single live stream for kiosk UIs."""

    name: str
    """Stream name / path on the MediaMTX server."""

    whep_url: str
    """WHEP WebRTC endpoint for consuming this stream."""

    stream_up: bool
    """Whether the stream is currently live."""

    webrtc_clients: int
    """Active WebRTC consumer count."""


class MediaRouter(Protocol):
    async def start_recording(
        self,
        *,
        session_id: UUID,
        kind: RecordingKind,
        stream: str,
    ) -> RecordingHandle:
        """Start a recording without blocking live playback."""
        ...

    async def finalize_recording(
        self,
        handle: RecordingHandle,
        *,
        consent_context: ConsentContext,
    ) -> FinalizedRecording:
        """Finalize a recording and return durable metadata."""
        ...

    async def discard_recording(self, handle: RecordingHandle) -> None:
        """Abort an unfinalized recording without creating a clip."""
        ...

    async def capture_photo(
        self,
        *,
        session_id: UUID,
        stream: str,
    ) -> CapturedPhoto:
        """Capture a visitor-camera still image for explicit photo-booth review."""
        ...

    async def snapshot(self) -> bytes | None:
        """Return a single current JPEG frame from the live stream, or None.

        Best-effort and non-blocking-ish: implementations must return ``None``
        (never raise) when no live frame is available so ``GET /snapshot`` can
        fall back to a placeholder. Returns ``None`` in mock mode (no camera).
        """
        ...

    def storage_status(self) -> MediaStorageStatus:
        """Return storage state used by retention and sync logic."""
        ...

    def stream_info(self) -> list[StreamInfo]:
        """Return current stream metadata for GET /streams."""
        ...

    async def health_check(self) -> bool:
        """Return True if the underlying media backend is healthy."""
        ...
