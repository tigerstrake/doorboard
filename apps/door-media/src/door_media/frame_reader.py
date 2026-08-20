"""One long-lived subprocess emitting MJPEG, newest frame kept in memory.

Extracted from :mod:`door_media.mediamtx_router` when a second camera arrived (ADR-0023).
The framing logic in particular is worth having exactly once: a JPEG's SOI and EOI
routinely land in different reads from a pipe, and getting that wrong presents as
recognition working *intermittently*, which is far harder to notice than it not working at
all. Duplicating it per camera would be duplicating that risk.

Two readers exist, and they differ only in the command they run:

- **visitor** — ffmpeg decoding the existing RTSP stream, so it stays a read-only consumer
  and does not disturb the publisher or MediaMTX's recording.
- **recognition** — ``rpicam-vid`` straight off the second camera in MJPEG. No RTSP hop and
  no H.264 encode at all: the face path wants stills, and the ~90% of a core an H.264
  encode costs would buy nothing it can use.

Both stop after an idle period, so an empty doorway and privacy mode cost no CPU, and the
next request restarts them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Sequence

logger = logging.getLogger("door_media.frame_reader")

# JPEG delimiters. MJPEG is complete JPEGs back to back.
_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"


class MjpegFrameReader:
    """Keeps the newest JPEG from a long-lived MJPEG-emitting subprocess."""

    def __init__(
        self,
        *,
        name: str,
        argv_factory: Callable[[], Sequence[str]],
        max_age_s: float,
        idle_stop_s: float,
        restart_backoff_max_s: float = 15.0,
    ) -> None:
        self._name = name
        self._argv_factory = argv_factory
        self._max_age_s = max_age_s
        self._idle_stop_s = idle_stop_s
        self._restart_backoff_max_s = restart_backoff_max_s

        self._latest_frame: bytes | None = None
        self._latest_frame_at = 0.0
        self._last_request_at = 0.0
        self._task: asyncio.Task[None] | None = None

        self.frames = 0
        self.restarts = 0
        # Frames handed out unchanged. Climbing steadily means the producer has stalled
        # and the doorway is being judged on a stale picture — recognition looks alive
        # while seeing nothing new.
        self.repeats = 0
        self._last_served: bytes | None = None

    # -- reading ------------------------------------------------------------

    def request(self) -> bytes | None:
        """The freshest frame, or None. Also marks the reader as wanted."""
        self._last_request_at = time.monotonic()
        self._ensure_running()
        frame = self._fresh_frame()
        if frame is not None and frame is self._last_served:
            self.repeats += 1
        self._last_served = frame
        return frame

    def _fresh_frame(self) -> bytes | None:
        """The cached frame, if recent enough to still describe the doorway.

        A stale frame is refused rather than served: a dead reader must not hide behind a
        plausible-looking picture of an empty doorway.
        """
        if self._latest_frame is None:
            return None
        if time.monotonic() - self._latest_frame_at > self._max_age_s:
            return None
        return self._latest_frame

    def _ensure_running(self) -> None:
        if self._task is not None and not self._task.done():
            return
        # Suppressed rather than required: `request()` is also reachable from sync callers
        # (tests, a health probe), and a reader that cannot start yet must not turn a
        # frame request into an exception on the face path.
        with contextlib.suppress(RuntimeError):
            self._task = asyncio.create_task(self._loop(), name=f"frame-reader-{self._name}")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._latest_frame = None

    # -- internals ----------------------------------------------------------

    async def _loop(self) -> None:
        """Hold one process open at a time, restarting it with capped backoff.

        Nothing here may take door-media down: recordings matter more than the face path,
        so every failure is logged and retried rather than raised.
        """
        backoff = 1.0
        while True:
            idle_s = time.monotonic() - self._last_request_at
            if idle_s > self._idle_stop_s:
                logger.info(
                    "frame_reader_idle_stop",
                    extra={"reader": self._name, "idle_s": round(idle_s, 1)},
                )
                self._latest_frame = None
                return
            try:
                await self._read_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:
                self.restarts += 1
                logger.warning(
                    "frame_reader_failed",
                    extra={"reader": self._name, "retry_in_s": backoff},
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(self._restart_backoff_max_s, backoff * 2)

    async def _read_once(self) -> None:
        """Run one producer until it exits, publishing each complete JPEG."""
        argv = list(self._argv_factory())
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info("frame_reader_started", extra={"reader": self._name, "argv0": argv[0]})
        try:
            assert proc.stdout is not None
            buffer = bytearray()
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                self._drain_frames(buffer)
                if time.monotonic() - self._last_request_at > self._idle_stop_s:
                    break
        finally:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()

    def _drain_frames(self, buffer: bytearray) -> None:
        """Publish every complete JPEG in *buffer*, keeping any partial tail.

        The SOI and EOI of one frame routinely arrive in different reads, so the tail must
        survive; and bytes before an SOI are junk from a mid-stream start and are dropped.
        """
        while True:
            start = buffer.find(_SOI)
            if start < 0:
                # Keep the last byte: it may be the first half of an SOI whose second
                # half arrives in the next read. Clearing outright discarded it and the
                # frame after it was lost — and because it only happens when a chunk
                # boundary falls *inside* the two-byte marker, it presents as recognition
                # working intermittently, which is exactly the failure T-310 called out
                # as harder to notice than an outright break.
                del buffer[: max(0, len(buffer) - (len(_SOI) - 1))]
                return
            end = buffer.find(_EOI, start + 2)
            if end < 0:
                del buffer[:start]
                return
            self._latest_frame = bytes(buffer[start : end + 2])
            self._latest_frame_at = time.monotonic()
            self.frames += 1
            del buffer[: end + 2]
