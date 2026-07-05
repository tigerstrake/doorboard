from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

from door_media.adapters import (
    ConsentContext,
    FinalizedRecording,
    MediaStorageStatus,
    RecordingHandle,
    RecordingKind,
)

from doorboard_simulator.clock import SimClock
from doorboard_simulator.ids import RECORDING_ID
from doorboard_simulator.outages import OutageState


class FakeMediaRouter:
    def __init__(
        self,
        clock: SimClock,
        events: object,
        outages: OutageState,
        *,
        artifact_root: Path,
    ) -> None:
        self._clock = clock
        self._outages = outages
        self._artifact_root = artifact_root
        self._recording_counter = 0

    async def start_recording(
        self,
        *,
        session_id: UUID,
        kind: RecordingKind,
        stream: str,
    ) -> RecordingHandle:
        self._recording_counter += 1
        return RecordingHandle(
            recording_id=RECORDING_ID,
            session_id=session_id,
            kind=kind,
            stream=stream,
            started_monotonic_ms=self._clock.monotonic_ms,
        )

    async def finalize_recording(
        self,
        handle: RecordingHandle,
        *,
        consent_context: ConsentContext,
    ) -> FinalizedRecording:
        path = Path("recordings") / f"{handle.recording_id}.mp4"
        full_path = self._artifact_root / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            f"doorboard simulator\nrecording_id={handle.recording_id}\n"
            f"started={handle.started_monotonic_ms}\nended={self._clock.monotonic_ms}\n"
        ).encode()
        full_path.write_bytes(content)
        duration_s = max(0.001, (self._clock.monotonic_ms - handle.started_monotonic_ms) / 1000)
        return FinalizedRecording(
            recording_id=handle.recording_id,
            path=path.as_posix(),
            duration_s=duration_s,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            consent_context=consent_context,
        )

    def storage_status(self) -> MediaStorageStatus:
        if self._outages.storage_full:
            return MediaStorageStatus(
                free_bytes=25_000_000,
                queue_depth=0,
                oldest_unsynced_s=0,
                recording_allowed=False,
            )
        return MediaStorageStatus(
            free_bytes=5_000_000_000,
            queue_depth=0 if self._outages.nas_reachable else 3,
            oldest_unsynced_s=0 if self._outages.nas_reachable else 120,
            recording_allowed=True,
        )
