from __future__ import annotations

import asyncio
import os
import time
import uuid
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


class _FakeStream:
    """Stdout stub that EOFs immediately so the drain task exits at once."""

    async def readline(self) -> bytes:
        return b""


class _FakeProc:
    """Minimal stand-in for an asyncio subprocess used by the audio path.

    ``terminate``/``kill`` flip flags and set a returncode so
    ``_stop_audio_capture`` sees the process exit; ``communicate`` writes the
    output file the command was asked to produce (used by the mux ffmpeg).
    """

    def __init__(self, argv: list[str]) -> None:
        self.argv = argv
        self.returncode: int | None = None
        self.stdout = _FakeStream()
        self.terminated = False
        self.killed = False
        self._write_on_communicate: str | None = None

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._write_on_communicate is not None:
            Path(self._write_on_communicate).write_bytes(b"muxed-bytes")
        if self.returncode is None:
            self.returncode = 0
        return (b"", b"")

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _fake_exec_factory(calls: list[list[str]]):
    """Return a fake ``create_subprocess_exec`` recording every argv."""

    async def fake_exec(*args: str, **kwargs: object) -> _FakeProc:
        argv = list(args)
        calls.append(argv)
        proc = _FakeProc(argv)
        # A mux command (has -map) produces its output file on communicate().
        if "-map" in argv:
            proc._write_on_communicate = argv[-1]
        return proc

    return fake_exec


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


def test_run_on_init_is_video_only_even_when_audio_enabled(tmp_path: Path) -> None:
    # Audio does NOT go through MediaMTX (its live fmp4 recorder can't mux AAC —
    # it drops the RTSP publish ~10s in). Mic audio is captured separately and
    # muxed at finalize, so the publish command stays video-only regardless of
    # MEDIA_AUDIO_ENABLED.
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
    # No ALSA input, no audio encode, no mic device — video-only.
    assert "-f alsa" not in cmd
    assert "aac" not in cmd
    assert "plughw:CARD=TestMic,DEV=0" not in cmd
    assert "-c:v copy" in cmd
    assert "rtsp://127.0.0.1:8554/visitor" in cmd

    rendered = _render_config(cfg)
    assert cmd in rendered
    assert "-f alsa" not in rendered
    assert "aac" not in rendered
    assert "{" not in rendered and "}" not in rendered


def test_run_on_init_is_byte_for_byte_pre_audio_original(tmp_path: Path) -> None:
    # The video-only publish command must match the historical (pre-#109)
    # runOnInit exactly, whether audio is enabled or not.
    expected = (
        "sh -c 'rpicam-vid --width 1280 --height 720 --framerate 25 --codec h264 "
        "--libav-format h264 --profile baseline --level 4.1 --bitrate 2000000 "
        "--inline --flush 1 --timeout 0 --nopreview --output - | "
        "ffmpeg -nostats -loglevel error -fflags nobuffer -f h264 -r 25 -i pipe:0 "
        "-c:v copy -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/visitor'"
    )
    for audio_enabled in (False, True):
        cfg = Settings(
            SSD_DATA_ROOT=tmp_path,
            MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
            MEDIA_AUDIO_ENABLED=audio_enabled,
        )
        assert _build_run_on_init(cfg) == expected


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


@pytest.mark.anyio
async def test_start_recording_spawns_audio_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        MEDIA_AUDIO_ENABLED=True,
        MEDIA_AUDIO_DEVICE="plughw:CARD=TestMic,DEV=0",
        MEDIA_AUDIO_SAMPLE_RATE=44100,
        MEDIA_AUDIO_BITRATE="128k",
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec_factory(calls))

    router = MediaMTXRouter(cfg)
    handle = await router.start_recording(
        session_id=uuid.uuid4(), kind="video_message", stream="visitor"
    )

    assert len(calls) == 1
    argv = " ".join(calls[0])
    assert calls[0][0] == "ffmpeg"
    assert "-nostdin" in argv
    assert "-f alsa" in argv
    assert "-ar 44100" in argv
    assert "-i plughw:CARD=TestMic,DEV=0" in argv
    assert "-c:a aac" in argv
    assert "-b:a 128k" in argv
    assert f"{handle.recording_id}.m4a" in argv

    active = router._active[handle.recording_id]
    assert active.audio_proc is not None
    assert active.audio_path is not None
    assert active.audio_path.name == f"{handle.recording_id}.m4a"

    await router.discard_recording(handle)


@pytest.mark.anyio
async def test_start_recording_video_only_when_audio_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
    )
    assert cfg.audio_enabled is False
    calls: list[list[str]] = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec_factory(calls))

    router = MediaMTXRouter(cfg)
    handle = await router.start_recording(
        session_id=uuid.uuid4(), kind="video_message", stream="visitor"
    )

    assert calls == []
    active = router._active[handle.recording_id]
    assert active.audio_proc is None
    assert active.audio_path is None


@pytest.mark.anyio
async def test_finalize_muxes_captured_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        MEDIA_AUDIO_ENABLED=True,
        MEDIA_AUDIO_SYNC_OFFSET_S=0.25,
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec_factory(calls))

    async def fake_concat(segments: list[Path], out_path: Path) -> None:
        Path(out_path).write_bytes(b"video-bytes")

    monkeypatch.setattr("door_media.mediamtx_router._concat_segments", fake_concat)

    router = MediaMTXRouter(cfg)
    handle = await router.start_recording(
        session_id=uuid.uuid4(), kind="video_message", stream="visitor"
    )
    active = router._active[handle.recording_id]
    assert active.audio_path is not None
    audio_path = active.audio_path
    audio_path.write_bytes(b"audio-bytes")

    _touch(cfg.segments_root / "visitor" / "seg.mp4", modified=time.time())

    result = await router.finalize_recording(handle, consent_context="visitor_initiated")

    mux_calls = [c for c in calls if "-map" in c]
    assert len(mux_calls) == 1
    argv = " ".join(mux_calls[0])
    out_path = cfg.recordings_root / f"video_message_{handle.recording_id}.mp4"
    assert str(out_path) in argv
    assert str(audio_path) in argv
    assert "-itsoffset 0.25" in argv
    assert "-map 0:v:0" in argv
    assert "-map 1:a:0" in argv
    assert "-c copy" in argv

    # out_path was atomically replaced by the muxed file; size/sha reflect it.
    assert out_path.read_bytes() == b"muxed-bytes"
    assert result.size_bytes == len(b"muxed-bytes")
    # Temp audio cleaned up; recording no longer active.
    assert not audio_path.exists()
    assert handle.recording_id not in router._active


@pytest.mark.anyio
async def test_finalize_video_only_when_audio_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
    )
    assert cfg.audio_enabled is False
    calls: list[list[str]] = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec_factory(calls))

    async def fake_concat(segments: list[Path], out_path: Path) -> None:
        Path(out_path).write_bytes(b"video-bytes")

    monkeypatch.setattr("door_media.mediamtx_router._concat_segments", fake_concat)

    router = MediaMTXRouter(cfg)
    handle = await router.start_recording(
        session_id=uuid.uuid4(), kind="video_message", stream="visitor"
    )
    _touch(cfg.segments_root / "visitor" / "seg.mp4", modified=time.time())

    result = await router.finalize_recording(handle, consent_context="visitor_initiated")

    # No audio process, no mux — the video-only file is returned unchanged.
    assert calls == []
    out_path = cfg.recordings_root / f"video_message_{handle.recording_id}.mp4"
    assert out_path.read_bytes() == b"video-bytes"
    assert result.size_bytes == len(b"video-bytes")


@pytest.mark.anyio
async def test_finalize_video_only_when_audio_file_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        MEDIA_AUDIO_ENABLED=True,
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec_factory(calls))

    async def fake_concat(segments: list[Path], out_path: Path) -> None:
        Path(out_path).write_bytes(b"video-bytes")

    monkeypatch.setattr("door_media.mediamtx_router._concat_segments", fake_concat)

    router = MediaMTXRouter(cfg)
    handle = await router.start_recording(
        session_id=uuid.uuid4(), kind="video_message", stream="visitor"
    )
    active = router._active[handle.recording_id]
    assert active.audio_path is not None
    active.audio_path.write_bytes(b"")  # capture produced nothing usable

    _touch(cfg.segments_root / "visitor" / "seg.mp4", modified=time.time())

    result = await router.finalize_recording(handle, consent_context="visitor_initiated")

    # Audio was spawned (1 call) but the empty file means NO mux.
    assert not any("-map" in c for c in calls)
    out_path = cfg.recordings_root / f"video_message_{handle.recording_id}.mp4"
    assert out_path.read_bytes() == b"video-bytes"
    assert result.size_bytes == len(b"video-bytes")


@pytest.mark.anyio
async def test_discard_stops_audio_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        MEDIA_AUDIO_ENABLED=True,
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec_factory(calls))

    router = MediaMTXRouter(cfg)
    handle = await router.start_recording(
        session_id=uuid.uuid4(), kind="video_message", stream="visitor"
    )
    active = router._active[handle.recording_id]
    proc = active.audio_proc
    audio_path = active.audio_path
    assert isinstance(proc, _FakeProc)
    assert audio_path is not None
    audio_path.write_bytes(b"audio-bytes")

    await router.discard_recording(handle)

    assert proc.terminated is True
    assert not audio_path.exists()
    assert handle.recording_id not in router._active
