"""Shared fixtures/helpers for door-sync tests.

The workspace runs pytest with ``--import-mode=importlib``, so test modules
cannot ``import`` each other's conftest by name. Shared helpers are therefore
exposed through the :func:`helpers` fixture (a ``SimpleNamespace``) rather than a
module import.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from door_sync._uuid7 import uuid7
from door_sync.engine import SyncEngine
from door_sync.media_client import MediaClient
from door_sync.queue import UploadQueue
from door_sync.settings import Settings
from door_sync.targets import (
    FilesystemNasTarget,
    MockMediaTarget,
    MockNucTarget,
    TransientError,
    sha256_file,
)
from doorboard_contracts.events import (
    MediaRecordingFinalizedEvent,
    MediaRecordingFinalizedPayload,
    MediaRecordingStartedEvent,
    MediaRecordingStartedPayload,
    MediaThumbnailReadyEvent,
    MediaThumbnailReadyPayload,
    SessionEndedEvent,
    SessionEndedPayload,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "door_id": "primary",
        "ssd_data_root": tmp_path / "ssd",
        "media_target": "mock",
        "control_plane_url": "",
        "ingest_token": "",
        "nas_sync_target": str(tmp_path / "nas"),
        "door_media_url": "http://127.0.0.1:9",
        "backoff_base_s": 0.0,
        "backoff_max_s": 0.0,
        "max_permanent_attempts": 3,
        "completed_retention_s": 0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class RecordingMediaClient(MediaClient):
    """Fake door-media that records deletion-license callbacks."""

    def __init__(self) -> None:
        self.synced: list[tuple[str, str]] = []
        self.pending: list[dict] = []
        self.notify_should_fail = False
        self.deletion_licensed: set[str] = set()

    async def list_pending_clips(self) -> list[dict]:
        return list(self.pending)

    async def notify_synced(self, *, recording_id, verified_sha256, item_id, attempts) -> None:  # noqa: ANN001
        if self.notify_should_fail:
            raise TransientError("door-media down")
        self.synced.append((str(recording_id), verified_sha256))
        self.deletion_licensed.add(str(recording_id))


class VerifyingDoorMedia(MediaClient):
    """Fake door-media that models the deletion license as a hard invariant:
    it will only accept a sync-completed notification if the NAS archive already
    holds a matching, checksum-valid copy. Any earlier notification (a premature
    deletion license) raises, failing the test at the moment of the violation —
    which is exactly what "never delete unverified" must guarantee.
    """

    def __init__(self, nas_root: Path, dest_keys: dict[str, str]) -> None:
        self._nas_root = nas_root
        self._dest_keys = dest_keys  # recording_id -> dest_key
        self.licensed: set[str] = set()

    async def list_pending_clips(self) -> list[dict]:
        return []

    async def notify_synced(self, *, recording_id, verified_sha256, item_id, attempts) -> None:  # noqa: ANN001
        rid = str(recording_id)
        dest = self._nas_root / self._dest_keys[rid]
        if not dest.exists():
            msg = f"PREMATURE DELETION LICENSE: archive for {rid} not present"
            raise AssertionError(msg)
        if sha256_file(dest) != verified_sha256:
            msg = f"PREMATURE DELETION LICENSE: archive for {rid} does not match"
            raise AssertionError(msg)
        self.licensed.add(rid)


def make_recording_file(ssd_root: Path, name: str = "clip.mp4") -> tuple[str, str, Path]:
    rel = f"recordings/{name}"
    abs_path = ssd_root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    data = f"video-bytes-{name}".encode() * 100
    abs_path.write_bytes(data)
    return rel, hashlib.sha256(data).hexdigest(), abs_path


def make_session_event_dict(door_id: str = "primary") -> dict:
    ev = SessionEndedEvent(
        event_id=uuid7(),
        type="session.ended",
        source="door-api",
        occurred_at=datetime.now(UTC),
        monotonic_ms=123,
        door_id=door_id,
        trace_id=uuid7(),
        payload=SessionEndedPayload(session_id=uuid7(), outcome="answered"),
    )
    return ev.model_dump(mode="json")


def make_media_recording_started_dict(
    *, recording_id=None, session_id=None, door_id: str = "primary"
) -> dict:  # noqa: ANN001
    ev = MediaRecordingStartedEvent(
        event_id=uuid7(),
        type="media.recording_started",
        source="door-media",
        occurred_at=datetime.now(UTC),
        monotonic_ms=123,
        door_id=door_id,
        trace_id=uuid7(),
        payload=MediaRecordingStartedPayload(
            recording_id=recording_id or uuid7(),
            session_id=session_id or uuid7(),
            kind="video_message",
            stream="entry",
        ),
    )
    return ev.model_dump(mode="json")


def make_media_recording_finalized_dict(
    *, recording_id=None, path: str = "recordings/clip.mp4", door_id: str = "primary"
) -> dict:  # noqa: ANN001
    data = b"video-bytes" * 100
    ev = MediaRecordingFinalizedEvent(
        event_id=uuid7(),
        type="media.recording_finalized",
        source="door-media",
        occurred_at=datetime.now(UTC),
        monotonic_ms=123,
        door_id=door_id,
        trace_id=uuid7(),
        payload=MediaRecordingFinalizedPayload(
            recording_id=recording_id or uuid7(),
            path=path,
            duration_s=4.5,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            consent_context="visitor_initiated",
        ),
    )
    return ev.model_dump(mode="json")


def make_media_thumbnail_ready_dict(
    *, recording_id=None, path: str = "thumbnails/clip.jpg", door_id: str = "primary"
) -> dict:  # noqa: ANN001
    ev = MediaThumbnailReadyEvent(
        event_id=uuid7(),
        type="media.thumbnail_ready",
        source="door-media",
        occurred_at=datetime.now(UTC),
        monotonic_ms=123,
        door_id=door_id,
        trace_id=uuid7(),
        payload=MediaThumbnailReadyPayload(recording_id=recording_id or uuid7(), path=path),
    )
    return ev.model_dump(mode="json")


@pytest.fixture
def helpers() -> SimpleNamespace:
    return SimpleNamespace(
        make_settings=make_settings,
        make_recording_file=make_recording_file,
        make_session_event_dict=make_session_event_dict,
        make_media_recording_started_dict=make_media_recording_started_dict,
        make_media_recording_finalized_dict=make_media_recording_finalized_dict,
        make_media_thumbnail_ready_dict=make_media_thumbnail_ready_dict,
        RecordingMediaClient=RecordingMediaClient,
        VerifyingDoorMedia=VerifyingDoorMedia,
        MockMediaTarget=MockMediaTarget,
        MockNucTarget=MockNucTarget,
        FilesystemNasTarget=FilesystemNasTarget,
        sha256_file=sha256_file,
    )


@pytest.fixture
def engine_env(tmp_path: Path):
    """A wired engine with mock targets and a fresh queue on disk."""
    settings = make_settings(tmp_path)
    queue = UploadQueue(settings.queue_db_path)
    media = MockMediaTarget()
    nuc = MockNucTarget()
    media_client = RecordingMediaClient()
    engine = SyncEngine(
        queue=queue,
        settings=settings,
        media_target=media,
        nuc_target=nuc,
        media_client=media_client,
    )
    yield engine, queue, media, nuc, media_client, settings
    queue.close()
