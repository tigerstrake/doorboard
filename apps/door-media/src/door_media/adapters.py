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
class MediaStorageStatus:
    free_bytes: int
    queue_depth: int
    oldest_unsynced_s: int
    recording_allowed: bool


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

    def storage_status(self) -> MediaStorageStatus:
        """Return storage state used by retention and sync logic."""
        ...
