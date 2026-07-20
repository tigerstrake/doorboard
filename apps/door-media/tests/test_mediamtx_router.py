from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from door_media.mediamtx_router import (
    MediaMTXRouter,
    _build_run_on_init,
    _find_segments,
    _prune_segments,
)
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


def _render_config(cfg: Settings) -> str:
    """Render the MediaMTX config through the real write path and return it."""
    router = MediaMTXRouter(cfg)
    router._write_config()
    return cfg.mediamtx_config_path.read_text(encoding="utf-8")


def test_run_on_init_video_only_when_audio_disabled(tmp_path: Path) -> None:
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
    )
    assert cfg.audio_enabled is False

    cmd = _build_run_on_init(cfg)
    # Video-only: no ALSA input and no audio encode.
    assert "-f alsa" not in cmd
    assert "aac" not in cmd
    assert "-c:v copy" in cmd
    assert "rtsp://127.0.0.1:8554/visitor" in cmd

    # The rendered config must embed exactly that command and leave no
    # unrendered template placeholders behind.
    rendered = _render_config(cfg)
    assert cmd in rendered
    assert "-f alsa" not in rendered
    assert "{" not in rendered and "}" not in rendered


def test_run_on_init_includes_audio_when_enabled(tmp_path: Path) -> None:
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        MEDIA_AUDIO_ENABLED=True,
        MEDIA_AUDIO_DEVICE="plughw:CARD=TestMic,DEV=0",
        MEDIA_AUDIO_SAMPLE_RATE=44100,
        MEDIA_AUDIO_BITRATE="128k",
    )
    assert cfg.audio_enabled is True

    cmd = _build_run_on_init(cfg)
    # Second ffmpeg input pulls the configured USB mic via ALSA and encodes AAC…
    assert "-f alsa" in cmd
    assert "-ar 44100" in cmd
    assert "-i plughw:CARD=TestMic,DEV=0" in cmd
    assert "-c:a aac" in cmd
    assert "-b:a 128k" in cmd
    # …while video is still copied and the RTSP publish URL is unchanged.
    assert "-c:v copy" in cmd
    assert "rtsp://127.0.0.1:8554/visitor" in cmd

    rendered = _render_config(cfg)
    assert cmd in rendered
    assert "-f alsa" in rendered
    assert "plughw:CARD=TestMic,DEV=0" in rendered
    assert "{" not in rendered and "}" not in rendered


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
