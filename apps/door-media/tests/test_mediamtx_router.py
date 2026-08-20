from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from door_media.mediamtx_router import (
    MediaMTXRouter,
    _build_run_on_init,
    _find_segments,
    _prune_segments,
    _segment_start_epoch,
)
from door_media.settings import Settings
from pydantic import ValidationError


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


def _mediamtx_segment_path(root: Path, stream: str, started: datetime) -> Path:
    """Build a MediaMTX-style segment path for ``started`` (%Y/%m/%d/%H/%M/%S-%f)."""
    micros = f"{started.microsecond:06d}"
    return (
        root
        / stream
        / f"{started.year:04d}"
        / f"{started.month:02d}"
        / f"{started.day:02d}"
        / f"{started.hour:02d}"
        / f"{started.minute:02d}"
        / f"{started.second:02d}-{micros}.mp4"
    )


def test_segment_start_epoch_parses_mediamtx_path(tmp_path: Path) -> None:
    # recordPath: {segments_root}/{stream}/%Y/%m/%d/%H/%M/%S-%f, %f = 6-digit
    # microseconds, local time (MediaMTX time.Local). Parsing must invert the
    # datetime the path encodes.
    started = datetime(2026, 7, 18, 14, 30, 45, 500000)
    seg = _mediamtx_segment_path(tmp_path / "segments", "visitor", started)

    assert _segment_start_epoch(seg) == pytest.approx(started.timestamp())


def test_segment_start_epoch_rejects_non_mediamtx_path(tmp_path: Path) -> None:
    # A filename that is not "%S-%f" (no separator / non-numeric) must raise so
    # the caller falls back to the file mtime.
    with pytest.raises((ValueError, IndexError)):
        _segment_start_epoch(tmp_path / "segments" / "visitor" / "seg.mp4")


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


def test_run_on_init_is_identical_whether_audio_is_enabled(tmp_path: Path) -> None:
    """The publish command is video-only, and enabling audio must not perturb it.

    This used to pin the whole command as a literal string, which made it a test of the
    encode settings rather than of audio-independence. Audio going through MediaMTX
    breaks its fmp4 recorder (#109), which is the actual thing worth guarding; the
    quality parameters are asserted separately so they can be tuned without editing an
    unrelated expectation.
    """
    commands = {
        _build_run_on_init(
            Settings(
                SSD_DATA_ROOT=tmp_path,
                MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
                MEDIA_AUDIO_ENABLED=audio_enabled,
            )
        )
        for audio_enabled in (False, True)
    }

    assert len(commands) == 1
    command = commands.pop()
    for audio_flag in ("-c:a", "aac", "--codec libav", "alsa"):
        assert audio_flag not in command


def test_run_on_init_publishes_at_the_configured_quality(tmp_path: Path) -> None:
    """Encode settings reach rpicam-vid, including the three that were missing.

    Shipped defaults were 720p/2Mbps/baseline with no autofocus mode and no tuning file,
    on a NoIR sensor — a soft, washed-out picture that no amount of bitrate would fix
    (T-314). ffmpeg's `-r` has to track the framerate too: it copies the stream, so a
    mismatch mistimes every frame.
    """
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        DOOR_MEDIA_VIDEO_WIDTH=1920,
        DOOR_MEDIA_VIDEO_HEIGHT=1080,
        DOOR_MEDIA_VIDEO_FRAMERATE=30,
        DOOR_MEDIA_VIDEO_BITRATE_BPS=8_000_000,
        DOOR_MEDIA_VIDEO_H264_PROFILE="high",
        DOOR_MEDIA_VIDEO_AUTOFOCUS_MODE="continuous",
        DOOR_MEDIA_CAMERA_TUNING_FILE="/usr/share/libcamera/ipa/rpi/pisp/imx708_noir.json",
        VISITOR_CAM_INDEX=1,
    )

    command = _build_run_on_init(cfg)

    assert "--width 1920 --height 1080" in command
    assert "--bitrate 8000000" in command
    assert "--profile high" in command
    assert "--autofocus-mode continuous" in command
    assert "--tuning-file /usr/share/libcamera/ipa/rpi/pisp/imx708_noir.json" in command
    assert "--camera 1" in command
    # Both sides of the pipe must agree on the rate, or copied frames are mistimed.
    assert "--framerate 30" in command
    assert "-r 30 -i pipe:0" in command


def test_tuning_file_can_be_left_to_libcamera(tmp_path: Path) -> None:
    """An empty tuning path must omit the flag, not pass an empty one."""
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        DOOR_MEDIA_CAMERA_TUNING_FILE="",
    )

    assert "--tuning-file" not in _build_run_on_init(cfg)


def test_the_default_profile_stays_baseline(tmp_path: Path) -> None:
    """B-frames break this pipeline, so `baseline` is a constraint and not a preference.

    `main`/`high` enable B-frames, and the publish copies rpicam-vid's elementary stream
    with a synthetic framerate, so there are no real timestamps to reorder them against.
    Verified on the door: at `high` the MediaMTX path reported `ready` with a live
    publisher and every /snapshot came back a placeholder — recognition sees nothing while
    everything upstream looks healthy. Raising it needs real PTS in the publish first.
    """
    cfg = Settings(SSD_DATA_ROOT=tmp_path, MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml")

    assert cfg.video_h264_profile == "baseline"
    assert "--profile baseline" in _build_run_on_init(cfg)


def test_an_unsupported_h264_profile_fails_at_startup(tmp_path: Path) -> None:
    """rpicam-vid rejects it at launch, where the failure looks like a dead camera."""
    with pytest.raises(ValidationError):
        Settings(
            SSD_DATA_ROOT=tmp_path,
            MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
            DOOR_MEDIA_VIDEO_H264_PROFILE="ultra",
        )


def test_an_unsupported_autofocus_mode_fails_at_startup(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(
            SSD_DATA_ROOT=tmp_path,
            MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
            DOOR_MEDIA_VIDEO_AUTOFOCUS_MODE="sometimes",
        )


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
async def test_finalize_trims_and_muxes_captured_audio(
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

    # A MediaMTX-style segment whose start precedes the real recording start by
    # 2.5s (the padded/boundary-snapped pre-roll). The real window is 6.496s
    # (matching the on-hardware audio track), so the final ffmpeg must -ss 2.5
    # into the concat and cap it at -t 6.496.
    seg_started = datetime(2026, 7, 18, 14, 30, 45, 500000)
    seg = _mediamtx_segment_path(cfg.segments_root, "visitor", seg_started)
    active.started_epoch = seg_started.timestamp() + 2.5
    finalize_epoch = active.started_epoch + 6.496
    monkeypatch.setattr(time, "time", lambda: finalize_epoch)
    _touch(seg, modified=active.started_epoch)  # inside the padded find window

    result = await router.finalize_recording(handle, consent_context="visitor_initiated")

    mux_calls = [c for c in calls if "-map" in c]
    assert len(mux_calls) == 1
    mux = mux_calls[0]
    argv = " ".join(mux)
    out_path = cfg.recordings_root / f"video_message_{handle.recording_id}.mp4"
    # Frame-accurate trim: input -ss BEFORE -i, then a re-encode.
    assert mux[0] == "ffmpeg"
    assert mux.index("-ss") < mux.index("-i")
    assert float(mux[mux.index("-ss") + 1]) == pytest.approx(2.5, abs=1e-3)
    assert float(mux[mux.index("-t") + 1]) == pytest.approx(6.496, abs=1e-3)
    assert str(out_path) in argv
    assert str(audio_path) in argv
    assert "-itsoffset 0.25" in argv
    assert "-c:v libx264" in argv
    assert "-preset veryfast" in argv
    assert "-crf 23" in argv
    assert "-pix_fmt yuv420p" in argv
    assert "-c:a copy" in argv
    assert "-map 0:v:0" in argv
    assert "-map 1:a:0" in argv

    # out_path was atomically replaced by the trimmed+muxed file; size/sha reflect it.
    assert out_path.read_bytes() == b"muxed-bytes"
    assert result.size_bytes == len(b"muxed-bytes")
    # Temp audio cleaned up; recording no longer active.
    assert not audio_path.exists()
    assert handle.recording_id not in router._active


@pytest.mark.anyio
async def test_finalize_falls_back_to_video_only_on_mux_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Audio was captured and the trim window is computable, but the trim/mux
    # ffmpeg fails → keep the untrimmed video-only concat, no exception.
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        MEDIA_AUDIO_ENABLED=True,
    )
    calls: list[list[str]] = []

    async def failing_exec(*args: str, **kwargs: object) -> _FakeProc:
        argv = list(args)
        calls.append(argv)
        proc = _FakeProc(argv)
        if "-map" in argv:
            proc.returncode = 1  # mux fails and writes no output file
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_exec)

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
    active.started_epoch = time.time() - 5  # non-zero real window

    _touch(cfg.segments_root / "visitor" / "seg.mp4", modified=time.time())

    result = await router.finalize_recording(handle, consent_context="visitor_initiated")

    # The trim/mux was attempted (a -map call) but failed.
    assert any("-map" in c for c in calls)
    out_path = cfg.recordings_root / f"video_message_{handle.recording_id}.mp4"
    assert out_path.read_bytes() == b"video-bytes"
    assert result.size_bytes == len(b"video-bytes")
    # Temp audio still cleaned up; recording no longer active.
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


# ---------------------------------------------------------------------------
# RTSP port single-sourcing
# ---------------------------------------------------------------------------


def test_rtsp_port_is_single_sourced(tmp_path: Path) -> None:
    # A non-default MEDIAMTX_RTSP_PORT must flow to every place the port is used:
    # the generated server config's rtspAddress, the rpicam publisher URL, and
    # the read-only consumer URL — so the setting can never silently disagree.
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        MEDIAMTX_RTSP_PORT=8600,
    )
    assert cfg.mediamtx_rtsp_url("visitor") == "rtsp://127.0.0.1:8600/visitor"

    cmd = _build_run_on_init(cfg)
    assert "rtsp://127.0.0.1:8600/visitor" in cmd

    rendered = _render_config(cfg)
    assert "rtspAddress: 127.0.0.1:8600" in rendered
    assert "{" not in rendered and "}" not in rendered


# ---------------------------------------------------------------------------
# Snapshot (GET /snapshot frame grab)
# ---------------------------------------------------------------------------


class _SnapshotProc:
    """Fake asyncio subprocess for the snapshot RTSP frame grab.

    ``communicate`` returns the configured stdout/stderr and returncode. With
    ``hang=True`` it sleeps indefinitely so ``asyncio.wait_for`` times out,
    exercising the timeout/kill path.
    """

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._final_rc = returncode
        self._hang = hang
        self.returncode: int | None = None
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            await asyncio.sleep(3600)
        self.returncode = self._final_rc
        return (self._stdout, self._stderr)

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = -9
        return self.returncode


def _snapshot_exec_factory(proc: _SnapshotProc, calls: list[list[str]]):
    async def fake_exec(*args: str, **kwargs: object) -> _SnapshotProc:
        calls.append(list(args))
        return proc

    return fake_exec


@pytest.mark.anyio
async def test_snapshot_returns_jpeg_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = Settings(SSD_DATA_ROOT=tmp_path, MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml")
    jpeg = b"\xff\xd8\xff\xe0real-frame\xff\xd9"
    proc = _SnapshotProc(stdout=jpeg, returncode=0)
    calls: list[list[str]] = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _snapshot_exec_factory(proc, calls))

    router = MediaMTXRouter(cfg)
    result = await router.snapshot()

    assert result == jpeg
    # A single read-only RTSP frame grab to stdout at the configured port/quality.
    argv = calls[0]
    assert argv[0] == "ffmpeg"
    assert "rtsp://127.0.0.1:8554/visitor" in argv
    assert argv[argv.index("-frames:v") + 1] == "1"
    assert argv[argv.index("-q:v") + 1] == str(cfg.snapshot_jpeg_quality)
    assert "pipe:1" in argv
    # Never writes a file to the SSD.
    assert list(tmp_path.rglob("*.jpg")) == []


@pytest.mark.anyio
async def test_snapshot_returns_none_on_ffmpeg_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Settings(SSD_DATA_ROOT=tmp_path, MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml")
    proc = _SnapshotProc(stdout=b"", stderr=b"Connection refused", returncode=1)
    calls: list[list[str]] = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _snapshot_exec_factory(proc, calls))

    router = MediaMTXRouter(cfg)
    assert await router.snapshot() is None


@pytest.mark.anyio
async def test_snapshot_returns_none_on_empty_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Settings(SSD_DATA_ROOT=tmp_path, MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml")
    # returncode 0 but no bytes (stream not yet publishing) → treated as failure.
    proc = _SnapshotProc(stdout=b"", returncode=0)
    calls: list[list[str]] = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _snapshot_exec_factory(proc, calls))

    router = MediaMTXRouter(cfg)
    assert await router.snapshot() is None


@pytest.mark.anyio
async def test_snapshot_times_out_and_kills_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        DOOR_MEDIA_SNAPSHOT_TIMEOUT_S=0.05,
    )
    proc = _SnapshotProc(hang=True)
    calls: list[list[str]] = []
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _snapshot_exec_factory(proc, calls))

    router = MediaMTXRouter(cfg)
    result = await router.snapshot()

    assert result is None
    assert proc.killed is True


@pytest.mark.anyio
async def test_snapshot_returns_none_when_ffmpeg_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Settings(SSD_DATA_ROOT=tmp_path, MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml")

    async def _raise(*args: str, **kwargs: object) -> object:
        raise FileNotFoundError("ffmpeg not installed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise)

    router = MediaMTXRouter(cfg)
    assert await router.snapshot() is None


class _MjpegStream:
    """Stdout stub that emits a scripted MJPEG byte sequence, then EOF."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


def _jpeg(marker: bytes) -> bytes:
    """A minimal JPEG-framed payload: SOI, body, EOI."""
    return b"\xff\xd8" + marker + b"\xff\xd9"


@pytest.mark.anyio
async def test_reader_publishes_each_frame_from_a_split_mjpeg_stream(tmp_path: Path) -> None:
    """The frame parser must handle MJPEG arriving in arbitrary chunks.

    ffmpeg writes into a pipe, so a JPEG's SOI and EOI routinely land in different
    reads. Getting this wrong looks like recognition working intermittently, which
    is far harder to spot than it not working at all.
    """
    cfg = Settings(SSD_DATA_ROOT=tmp_path)
    router = MediaMTXRouter(cfg)

    first, second = _jpeg(b"AAA"), _jpeg(b"BBB")
    stream = _MjpegStream(
        [
            first[:3],  # SOI split mid-marker
            first[3:] + second[:4],  # rest of one frame plus the head of the next
            second[4:],
        ]
    )

    class _Proc:
        def __init__(self) -> None:
            self.stdout = stream
            self.killed = False

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            return 0

    proc = _Proc()

    async def _fake_exec(*_args: str, **_kwargs: object) -> _Proc:
        return proc

    original = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = _fake_exec  # type: ignore[assignment]
    try:
        # The framing lives on the reader now (ADR-0023 extracted it so two cameras
        # cannot each get it subtly wrong); drive it there rather than through the router.
        reader = router._visitor_reader
        reader._last_request_at = time.monotonic()
        await reader._read_once()
    finally:
        asyncio.create_subprocess_exec = original  # type: ignore[assignment]

    assert router.reader_frames == 2
    # The newest frame wins, and it is a complete JPEG.
    assert reader._latest_frame == second
    assert proc.killed, "ffmpeg must be reaped when the reader stops"


@pytest.mark.anyio
async def test_a_stale_cached_frame_is_not_served(tmp_path: Path) -> None:
    """A frame older than max_age_s describes a doorway that has moved on.

    Serving it would make the door greet someone who has already left, and would
    hide a dead reader behind a plausible-looking image.
    """
    cfg = Settings(SSD_DATA_ROOT=tmp_path, DOOR_MEDIA_SNAPSHOT_MAX_AGE_S=0.5)
    router = MediaMTXRouter(cfg)
    reader = router._visitor_reader

    reader._latest_frame = _jpeg(b"fresh")
    reader._latest_frame_at = time.monotonic()
    assert reader._fresh_frame() == _jpeg(b"fresh")

    reader._latest_frame_at = time.monotonic() - 5.0
    assert reader._fresh_frame() is None


def test_rotation_reaches_the_camera_when_set(tmp_path: Path) -> None:
    """An inverted sensor makes recognition look broken, so this has to actually apply.

    ArcFace is not rotation invariant: an upside-down face scores near nothing against
    upright enrollment photos. Found on the door, where a bell-clip thumbnail came back
    inverted (T-321).
    """
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        DOOR_MEDIA_VIDEO_ROTATION=180,
    )

    assert "--rotation 180" in _build_run_on_init(cfg)


def test_no_orientation_flags_when_the_camera_is_upright(tmp_path: Path) -> None:
    cfg = Settings(SSD_DATA_ROOT=tmp_path, MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml")

    command = _build_run_on_init(cfg)

    assert "--rotation" not in command
    assert "--hflip" not in command
    assert "--vflip" not in command


def test_flips_are_independent_of_rotation(tmp_path: Path) -> None:
    """A mirrored mount is a different fault from an inverted one."""
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        DOOR_MEDIA_VIDEO_HFLIP=True,
    )

    command = _build_run_on_init(cfg)

    assert "--hflip" in command
    assert "--rotation" not in command


def test_an_impossible_rotation_fails_at_startup(tmp_path: Path) -> None:
    """rpicam-vid accepts only 0 or 180; 90 would fail as a dead camera."""
    with pytest.raises(ValidationError):
        Settings(
            SSD_DATA_ROOT=tmp_path,
            MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
            DOOR_MEDIA_VIDEO_ROTATION=90,
        )
