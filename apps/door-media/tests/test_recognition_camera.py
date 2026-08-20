"""The second camera, used for faces only (ADR-0023).

`VISION_MODE=dual-camera` was in the allowed-modes set and behaved *identically* to
`single-camera`: `HardwareBackend` took one snapshot URL and nothing plumbed a second
camera anywhere. A door could be configured for two cameras and quietly use one, with no
signal of any kind. These pin the two properties that make the mode real: the recognition
frames come off the other sensor, and an absent recognition camera says so rather than
substituting the visitor's.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from door_media.frame_reader import MjpegFrameReader
from door_media.mediamtx_router import MediaMTXRouter
from door_media.settings import Settings
from pydantic import ValidationError


def _jpeg(body: bytes) -> bytes:
    return b"\xff\xd8" + body + b"\xff\xd9"


def _cfg(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings with the two mandatory paths filled in.

    `Any` rather than `object` because these are pydantic aliases resolved at runtime;
    `object` makes every call a type error without catching anything real.
    """
    return Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        **overrides,
    )


class TestPresence:
    def test_absent_by_default(self, tmp_path: Path) -> None:
        """A single-camera door must not grow a second camera by upgrading."""
        cfg = _cfg(tmp_path)

        assert cfg.recognition_cam_present is False
        assert MediaMTXRouter(cfg)._recognition_reader is None

    def test_present_when_an_index_is_configured(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, RECOGNITION_CAM_INDEX=1)

        assert cfg.recognition_cam_present is True
        assert MediaMTXRouter(cfg)._recognition_reader is not None

    @pytest.mark.anyio
    async def test_no_camera_returns_none_not_the_visitor_frame(self, tmp_path: Path) -> None:
        """The substitution this whole design exists to prevent.

        Falling back to the visitor stream would hand the face path a wide-angle view of
        the doorway while reporting success — exactly the silent wrong-camera behaviour
        that made `dual-camera` meaningless.
        """
        router = MediaMTXRouter(_cfg(tmp_path))

        assert await router.recognition_snapshot() is None

    @pytest.mark.anyio
    async def test_a_cold_camera_is_waited_for_not_declared_missing(self, tmp_path: Path) -> None:
        """The flapping bug, from door-media's side.

        `request()` starts the reader and returns immediately, so a cold rpicam-vid has no
        frame for ~1-2 s. door-visiond polls every 100 ms and degrades its backend after
        three consecutive failures — 300 ms — so answering "nothing" straight away meant it
        killed the camera it had just started. The route now gives the reader a bounded
        chance to produce its first frame.
        """
        cfg = _cfg(tmp_path, RECOGNITION_CAM_INDEX=1, DOOR_MEDIA_RECOGNITION_FIRST_FRAME_WAIT_S=2.0)
        router = MediaMTXRouter(cfg)
        frame = _jpeg(b"warmed-up")
        calls = 0

        def slow_first_frame() -> bytes | None:
            nonlocal calls
            calls += 1
            # Nothing for the first few polls, exactly like a sensor opening.
            return frame if calls > 3 else None

        assert router._recognition_reader is not None
        router._recognition_reader.request = slow_first_frame  # type: ignore[method-assign]

        assert await router.recognition_snapshot() == frame
        assert calls > 3, "it must have polled again rather than giving up on the first None"
        assert router.recognition_cold_starts == 1

    @pytest.mark.anyio
    async def test_the_wait_is_bounded(self, tmp_path: Path) -> None:
        # A camera that never produces must not hang the route: door-visiond polls this on
        # an interval and the face path may never block for long.
        cfg = _cfg(tmp_path, RECOGNITION_CAM_INDEX=1, DOOR_MEDIA_RECOGNITION_FIRST_FRAME_WAIT_S=0.3)
        router = MediaMTXRouter(cfg)
        assert router._recognition_reader is not None
        router._recognition_reader.request = lambda: None  # type: ignore[method-assign]

        started = time.monotonic()
        assert await router.recognition_snapshot() is None
        assert time.monotonic() - started < 2.0


class TestArgv:
    def test_it_reads_the_other_sensor(self, tmp_path: Path) -> None:
        router = MediaMTXRouter(_cfg(tmp_path, RECOGNITION_CAM_INDEX=0, VISITOR_CAM_INDEX=1))

        argv = router._recognition_reader_argv()

        assert argv[0] == "rpicam-vid"
        assert "--camera" in argv
        assert argv[argv.index("--camera") + 1] == "0"

    def test_it_never_encodes_h264(self, tmp_path: Path) -> None:
        """Nothing consumes a recognition *stream*, so an encode buys the face path
        nothing and costs ~90% of a core."""
        router = MediaMTXRouter(_cfg(tmp_path, RECOGNITION_CAM_INDEX=1))

        argv = router._recognition_reader_argv()

        assert argv[argv.index("--codec") + 1] == "mjpeg"
        assert "h264" not in " ".join(argv)
        # And no RTSP hop: it reads the sensor directly.
        assert "rtsp" not in " ".join(argv)

    def test_the_visitor_reader_stays_a_read_only_rtsp_consumer(self, tmp_path: Path) -> None:
        """Re-opening the visitor sensor directly would contend with the publisher."""
        router = MediaMTXRouter(_cfg(tmp_path, RECOGNITION_CAM_INDEX=1))

        argv = router._visitor_reader_argv()

        assert argv[0] == "ffmpeg"
        assert any("rtsp://" in part for part in argv)

    def test_orientation_is_independent_of_the_visitor_camera(self, tmp_path: Path) -> None:
        """Two sensors mounted separately can be inverted separately (T-321)."""
        router = MediaMTXRouter(
            _cfg(
                tmp_path,
                RECOGNITION_CAM_INDEX=1,
                DOOR_MEDIA_RECOGNITION_ROTATION=180,
                DOOR_MEDIA_VIDEO_ROTATION=0,
            )
        )

        assert "--rotation" in router._recognition_reader_argv()
        assert "--rotation" not in router._visitor_reader_argv()

    def test_its_own_tuning_file(self, tmp_path: Path) -> None:
        """The two sensors are different NoIR variants; the wrong tuning washes out."""
        router = MediaMTXRouter(
            _cfg(
                tmp_path,
                RECOGNITION_CAM_INDEX=1,
                DOOR_MEDIA_RECOGNITION_TUNING_FILE="/tuning/narrow_noir.json",
                DOOR_MEDIA_CAMERA_TUNING_FILE="/tuning/wide_noir.json",
            )
        )

        recognition = router._recognition_reader_argv()

        assert recognition[recognition.index("--tuning-file") + 1] == "/tuning/narrow_noir.json"

    def test_an_impossible_rotation_fails_at_startup(self, tmp_path: Path) -> None:
        # Specifically a validation failure: a bare `Exception` would pass on a typo in
        # the alias just as happily, which is the opposite of what this guards.
        with pytest.raises(ValidationError):
            _cfg(tmp_path, RECOGNITION_CAM_INDEX=1, DOOR_MEDIA_RECOGNITION_ROTATION=90)


class TestSharedReader:
    """The framing logic exists once; both cameras get it right or neither does."""

    @pytest.mark.anyio
    async def test_a_jpeg_split_across_reads_is_reassembled(self) -> None:
        """SOI and EOI routinely land in different reads from a pipe. Getting this wrong
        presents as recognition working *intermittently*, which is much harder to notice
        than it not working at all."""
        first, second = _jpeg(b"one"), _jpeg(b"two")
        stream = first + second
        # Deliberately awkward boundaries: mid-SOI, mid-body, mid-EOI.
        chunks = [
            stream[:1],
            stream[1:4],
            stream[4 : len(first) + 1],
            stream[len(first) + 1 :],
            b"",
        ]

        class _Stdout:
            async def read(self, _n: int) -> bytes:
                return chunks.pop(0) if chunks else b""

        class _Proc:
            def __init__(self) -> None:
                self.stdout = _Stdout()
                self.killed = False

            def kill(self) -> None:
                self.killed = True

            async def wait(self) -> int:
                return 0

        proc = _Proc()

        async def _fake_exec(*_a: str, **_k: object) -> _Proc:
            return proc

        reader = MjpegFrameReader(
            name="test", argv_factory=lambda: ["true"], max_age_s=5.0, idle_stop_s=30.0
        )
        reader._last_request_at = time.monotonic()

        original = asyncio.create_subprocess_exec
        asyncio.create_subprocess_exec = _fake_exec  # type: ignore[assignment]
        try:
            await reader._read_once()
        finally:
            asyncio.create_subprocess_exec = original  # type: ignore[assignment]

        assert reader.frames == 2
        assert reader._latest_frame == second
        assert proc.killed, "the producer must be reaped"

    def test_a_stale_frame_is_refused(self) -> None:
        """A dead reader must not hide behind a plausible picture of an empty doorway."""
        reader = MjpegFrameReader(
            name="test", argv_factory=lambda: ["true"], max_age_s=0.5, idle_stop_s=30.0
        )
        reader._latest_frame = _jpeg(b"x")

        reader._latest_frame_at = time.monotonic()
        assert reader._fresh_frame() is not None

        reader._latest_frame_at = time.monotonic() - 5.0
        assert reader._fresh_frame() is None

    def test_repeated_frames_are_counted(self) -> None:
        """A climbing repeat count is how a stalled producer becomes visible: recognition
        looks alive while judging the doorway on one old picture."""
        reader = MjpegFrameReader(
            name="test", argv_factory=lambda: ["true"], max_age_s=30.0, idle_stop_s=30.0
        )
        reader._latest_frame = _jpeg(b"same")
        reader._latest_frame_at = time.monotonic()

        reader.request()
        assert reader.repeats == 0
        reader.request()
        reader.request()
        assert reader.repeats == 2
