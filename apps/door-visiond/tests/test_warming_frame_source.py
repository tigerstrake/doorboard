"""A warming-up frame source must not look like a broken backend.

The bug this pins down, measured on the door. `VISION_MODE=dual-camera` was switched on and
face recognition flapped: 12 degradations and 10 recoveries in 20 minutes, `/health`
alternating between `mode: dual-camera` and `mode: disabled  runtime_warning: vision backend
failed: HTTPError`.

The loop:

1. door-visiond polls `GET /snapshot/recognition`. The request *starts* door-media's reader
   and returns immediately, so a cold `rpicam-vid` has no frame for ~1-2 s.
2. door-media answered 404. door-visiond counts three consecutive frame errors — three
   polls at 100 ms, i.e. 300 ms — and degrades the backend to disabled.
3. Degraded, it stops polling. door-media's reader idle-stops after 30 s.
4. door-visiond's recovery delay is also 30 s, so the single retry lands exactly on the
   idle-stopped reader. Cold camera, 404, degrade. Forever.

So door-visiond disabled the very camera it had just asked door-media to start, and the two
timers were tuned to guarantee it kept happening. The fix splits "no recognition camera on
this door" (404, a real misconfiguration, still fatal) from "configured, not ready yet"
(503, a retry).
"""

from __future__ import annotations

import asyncio
import urllib.error
from typing import Any

import pytest
from door_visiond.embedder import MockEmbedder
from door_visiond.pipeline import HardwareBackend

_DIM = 512


class _FakePipeline:
    """Minimal stand-in for HailoFacePipeline; no cv2, no Hailo."""

    def __init__(self) -> None:
        self.calls = 0

    def embed_all(self, image_bytes: bytes) -> tuple[list[Any], float]:
        self.calls += 1
        return [], 1.0

    def close(self) -> None:
        pass


def _backend(**kwargs: Any) -> HardwareBackend:
    kwargs.setdefault("interval_ms", 0)
    return HardwareBackend(
        mode="dual-camera",
        embedder=MockEmbedder(dim=_DIM),
        snapshot_url="http://127.0.0.1:8082/snapshot/recognition",
        pipeline=_FakePipeline(),
        **kwargs,
    )


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://127.0.0.1:8082/snapshot/recognition",
        code=code,
        msg="test",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )


def test_a_warming_camera_yields_no_frame_instead_of_raising() -> None:
    """503 is "not ready", and the loop treats a None capture as an ordinary quiet tick."""
    backend = _backend()
    calls = 0

    def fake_open(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise _http_error(503)

    import urllib.request

    original = urllib.request.urlopen
    urllib.request.urlopen = fake_open  # type: ignore[assignment]
    try:
        # Would have raised before the fix, three times over, and taken the backend with it.
        for _ in range(3):
            assert asyncio.run(backend.next_capture()) is None
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]

    assert calls == 3
    assert backend.status().mode == "dual-camera", "a warm-up must not change the mode"


def test_a_missing_recognition_camera_still_raises() -> None:
    """404 means the door has no recognition camera — a misconfiguration, not a wait.

    This must stay loud. Swallowing it too would turn "the second camera is not configured"
    into "a door that silently never recognises anybody", which is the failure mode
    ADR-0023 exists to prevent.
    """
    backend = _backend()

    def fake_open(*_args: Any, **_kwargs: Any) -> Any:
        raise _http_error(404)

    import urllib.request

    original = urllib.request.urlopen
    urllib.request.urlopen = fake_open  # type: ignore[assignment]
    try:
        with pytest.raises(urllib.error.HTTPError):
            asyncio.run(backend.next_capture())
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]


def test_warming_polls_are_counted_separately_from_duplicates() -> None:
    # Duplicate frames and warm-up polls both produce no capture, but they mean different
    # things and a door that is warming up must not read as a door serving stale frames.
    backend = _backend()

    def fake_open(*_args: Any, **_kwargs: Any) -> Any:
        raise _http_error(503)

    import urllib.request

    original = urllib.request.urlopen
    urllib.request.urlopen = fake_open  # type: ignore[assignment]
    try:
        for _ in range(4):
            asyncio.run(backend.next_capture())
    finally:
        urllib.request.urlopen = original  # type: ignore[assignment]

    assert backend._source_warming_up == 4
    assert backend.status().duplicate_frames == 0
