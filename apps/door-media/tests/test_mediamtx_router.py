from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from door_media.mediamtx_router import MediaMTXRouter, _find_segments, _prune_segments
from door_media.settings import Settings


def _touch(path: Path, *, modified: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"segment")
    os.utime(path, (modified, modified))


def test_find_segments_filters_to_recording_window(tmp_path: Path) -> None:
    now = time.time()
    old = tmp_path / "old.mp4"
    current = tmp_path / "current.mp4"
    future = tmp_path / "future.mp4"
    _touch(old, modified=now - 30)
    _touch(current, modified=now - 5)
    _touch(future, modified=now + 30)

    assert _find_segments(
        tmp_path,
        started_epoch=now - 10,
        ended_epoch=now + 10,
    ) == [current]


def test_prune_segments_removes_only_expired_files(tmp_path: Path) -> None:
    now = time.time()
    expired = tmp_path / "nested" / "expired.mp4"
    retained = tmp_path / "retained.mp4"
    _touch(expired, modified=now - 120)
    _touch(retained, modified=now - 10)

    assert _prune_segments(tmp_path, older_than_epoch=now - 60) == 1
    assert not expired.exists()
    assert retained.exists()


@pytest.mark.anyio
async def test_router_prune_protects_active_recording_window(tmp_path: Path) -> None:
    now = time.time()
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        DOOR_MEDIA_SEGMENT_RETENTION_S=60,
    )
    router = MediaMTXRouter(cfg)
    handle = await router.start_recording(
        session_id=__import__("uuid").uuid4(),
        kind="bell_clip",
        stream="visitor",
    )
    router._active[handle.recording_id].started_epoch = now - 120
    protected = cfg.segments_root / "visitor" / "protected.mp4"
    stale = cfg.segments_root / "other" / "stale.mp4"
    _touch(protected, modified=now - 90)
    _touch(stale, modified=now - 180)

    assert router.prune_segments(now_epoch=now) == 1
    assert protected.exists()
    assert not stale.exists()
    await router.stop()
