"""A producer that dies is a failure, not a clean stop (audit P2).

``_read_once`` returned normally on EOF whatever the producer's exit status, so an
ffmpeg against a MediaMTX that is down, or an ``rpicam-vid`` with the module unplugged,
exited in milliseconds with nothing on stdout and was respawned immediately: measured
at ~600 spawns a second with ``/usr/bin/true`` as the producer, ``restarts`` never
moving, stderr discarded. These use real subprocesses (``python -c``) because the
thing under test is what happens when one exits.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time

import pytest
from door_media.frame_reader import MjpegFrameReader

_JPEG = b"\xff\xd8frame\xff\xd9"


def _producer(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.mark.anyio
async def test_a_producer_that_exits_without_frames_is_backed_off_not_respawned() -> None:
    spawns = 0

    def argv() -> list[str]:
        nonlocal spawns
        spawns += 1
        return _producer("import sys; sys.stderr.write('no cameras available\\n')")

    reader = MjpegFrameReader(name="test", argv_factory=argv, max_age_s=5.0, idle_stop_s=30.0)
    reader._last_request_at = time.monotonic()
    task = asyncio.create_task(reader._loop())
    try:
        await asyncio.sleep(1.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert spawns <= 3, f"{spawns} spawns in 1 s is the hot loop"
    assert reader.restarts >= 1, "the failure has to be visible on the counter"


@pytest.mark.anyio
async def test_the_failure_says_why() -> None:
    reader = MjpegFrameReader(
        name="test",
        argv_factory=lambda: _producer(
            "import sys; sys.stderr.write('rtsp://127.0.0.1:8554/visitor: Connection refused\\n');"
            " sys.exit(1)"
        ),
        max_age_s=5.0,
        idle_stop_s=30.0,
    )
    reader._last_request_at = time.monotonic()

    with pytest.raises(RuntimeError, match=r"rc=1 after 0 frames: .*Connection refused"):
        await reader._read_once()


@pytest.mark.anyio
async def test_a_non_zero_exit_after_frames_is_still_a_failure() -> None:
    reader = MjpegFrameReader(
        name="test",
        argv_factory=lambda: _producer(
            f"import sys; sys.stdout.buffer.write({_JPEG!r}); sys.stdout.flush(); sys.exit(2)"
        ),
        max_age_s=5.0,
        idle_stop_s=30.0,
    )
    reader._last_request_at = time.monotonic()

    with pytest.raises(RuntimeError, match="rc=2 after 1 frames"):
        await reader._read_once()
    assert reader.frames == 1


@pytest.mark.anyio
async def test_a_clean_exit_after_frames_is_not_a_failure() -> None:
    """A producer that streamed and then ended normally just gets restarted."""
    reader = MjpegFrameReader(
        name="test",
        argv_factory=lambda: _producer(
            f"import sys; sys.stdout.buffer.write({_JPEG!r} * 3); sys.stdout.flush()"
        ),
        max_age_s=5.0,
        idle_stop_s=30.0,
    )
    reader._last_request_at = time.monotonic()

    await reader._read_once()

    assert reader.frames == 3
    assert reader.restarts == 0


@pytest.mark.anyio
async def test_an_idle_stop_is_not_a_failure() -> None:
    """The reader stops a healthy producer when nothing has asked for a frame; that
    must not be counted as the producer dying."""
    reader = MjpegFrameReader(
        name="test",
        argv_factory=lambda: _producer(
            "import sys, time\n"
            f"sys.stdout.buffer.write({_JPEG!r}); sys.stdout.flush(); time.sleep(30)"
        ),
        max_age_s=5.0,
        idle_stop_s=0.05,
    )
    reader._last_request_at = time.monotonic() - 1.0  # already idle

    started = time.monotonic()
    await reader._read_once()

    assert time.monotonic() - started < 5.0, "the producer must be killed, not waited for"
    assert reader.restarts == 0
