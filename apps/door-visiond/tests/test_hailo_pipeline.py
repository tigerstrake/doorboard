"""T-305 — Hailo face path adapters, with the real inference mocked out.

These tests never touch a Hailo device, ``cv2`` or ``hailo_platform``: they
inject a fake :class:`HailoFacePipeline` (with the exact method surface the
adapters call) so the wiring — not the ML — is exercised.  The heavy
``door_visiond.hailo_pipeline`` module (which imports ``cv2``/``hailo_platform``)
is therefore never imported, keeping the suite import-safe in CI.
"""

from __future__ import annotations

import asyncio
import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest
from door_visiond import compat
from door_visiond.compat import check_compatibility
from door_visiond.embedder import HailoEmbedder, MockEmbedder
from door_visiond.embedding import Embedding
from door_visiond.pipeline import FrameCapture, HardwareBackend
from door_visiond.settings import (
    PINNED_HAILO_RUNTIME,
    PINNED_MODEL_DIM,
    PINNED_MODEL_ID,
    reset_settings,
)

_DIM = 512


@dataclass(frozen=True)
class _FakeFace:
    """Stand-in for hailo_pipeline.PipelineFace (avoids importing cv2)."""

    vector: tuple[float, ...]
    score: float
    size_px: int


class _FakePipeline:
    """Fake HailoFacePipeline: records calls, returns canned results."""

    def __init__(
        self,
        *,
        primary: _FakeFace | None = None,
        all_faces: list[_FakeFace] | None = None,
        inference_ms: float = 0.0,
    ) -> None:
        self._primary = primary
        self._all_faces = all_faces or []
        self._inference_ms = inference_ms
        self.embed_primary_calls = 0
        self.embed_all_calls = 0
        self.closed = False

    def embed_primary(self, image_bytes: bytes) -> _FakeFace | None:  # noqa: ARG002
        self.embed_primary_calls += 1
        return self._primary

    def embed_all(self, image_bytes: bytes) -> tuple[list[_FakeFace], float]:  # noqa: ARG002
        self.embed_all_calls += 1
        return list(self._all_faces), self._inference_ms

    def close(self) -> None:
        self.closed = True


def _fake(**kwargs: Any) -> Any:
    """Build a fake pipeline typed as Any (it duck-types HailoFacePipeline)."""
    return _FakePipeline(**kwargs)


# ---------------------------------------------------------------------------
# HailoEmbedder
# ---------------------------------------------------------------------------


def test_embedder_returns_512d_embedding_and_quality() -> None:
    face = _FakeFace(vector=tuple(0.1 for _ in range(_DIM)), score=0.87, size_px=180)
    embedder = HailoEmbedder(
        dim=_DIM, model_id=PINNED_MODEL_ID, pipeline=_fake(primary=face)
    )

    embedding, quality = embedder.embed(b"jpeg-bytes")

    assert isinstance(embedding, Embedding)
    assert embedding.dim == _DIM
    assert quality == pytest.approx(0.87)
    assert quality >= 0.5  # accepted by enrollment (min_enroll_quality 0.5)
    # The vector is never revealed in string forms.
    assert repr(embedding) == f"Embedding(dim={_DIM}, redacted)"


def test_embedder_rejects_no_face_with_low_quality() -> None:
    embedder = HailoEmbedder(
        dim=_DIM, model_id=PINNED_MODEL_ID, pipeline=_fake(primary=None)
    )

    embedding, quality = embedder.embed(b"jpeg-bytes-no-face")

    assert isinstance(embedding, Embedding)
    assert embedding.dim == _DIM  # placeholder vector, right shape
    assert quality < 0.5  # below min_enroll_quality -> enrollment rejects it
    assert quality == 0.0


def test_embedder_builds_pipeline_lazily_from_hef_paths() -> None:
    """No pipeline injected: it must be built lazily and only when needed."""
    built: list[dict[str, Any]] = []

    class _StubModule(types.ModuleType):
        pass

    stub = _StubModule("door_visiond.hailo_pipeline")

    def _factory(**kwargs: Any) -> _FakePipeline:
        built.append(kwargs)
        return _FakePipeline(primary=_FakeFace(tuple(0.0 for _ in range(_DIM)), 0.9, 90))

    stub.HailoFacePipeline = _factory  # type: ignore[attr-defined]

    embedder = HailoEmbedder(
        dim=_DIM,
        model_id=PINNED_MODEL_ID,
        detector_hef_path="/models/scrfd.hef",
        recognizer_hef_path="/models/arcface.hef",
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "door_visiond.hailo_pipeline", stub)
        _embedding, quality = embedder.embed(b"jpeg")

    assert quality == pytest.approx(0.9)
    assert built == [
        {
            "detector_hef_path": "/models/scrfd.hef",
            "recognizer_hef_path": "/models/arcface.hef",
            "model_id": PINNED_MODEL_ID,
            "dim": _DIM,
        }
    ]


def test_embedder_without_pipeline_or_paths_raises() -> None:
    embedder = HailoEmbedder(dim=_DIM, model_id=PINNED_MODEL_ID)
    with pytest.raises(RuntimeError, match="neither a pipeline nor HEF paths"):
        embedder.embed(b"jpeg")


# ---------------------------------------------------------------------------
# HardwareBackend
# ---------------------------------------------------------------------------


def _backend(pipeline: Any, **kwargs: Any) -> HardwareBackend:
    return HardwareBackend(
        mode="hardware",
        embedder=MockEmbedder(dim=_DIM),
        snapshot_url="http://127.0.0.1:8082/snapshot",
        pipeline=pipeline,
        interval_ms=0,
        **kwargs,
    )


def test_next_capture_builds_frame_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    faces = [
        _FakeFace(vector=tuple(0.2 for _ in range(_DIM)), score=0.82, size_px=150),
        _FakeFace(vector=tuple(0.3 for _ in range(_DIM)), score=0.71, size_px=90),
    ]
    pipeline = _FakePipeline(all_faces=faces, inference_ms=12.5)
    backend = _backend(pipeline)
    monkeypatch.setattr(backend, "_fetch_snapshot", lambda: b"jpeg-frame")

    capture = asyncio.run(backend.next_capture())

    assert isinstance(capture, FrameCapture)
    assert len(capture.faces) == 2
    assert capture.inference_ms == pytest.approx(12.5)
    first = capture.faces[0]
    assert first.size_px == 150
    assert first.quality == pytest.approx(0.82)
    assert isinstance(first.embedding, Embedding)
    assert first.embedding.dim == _DIM
    assert pipeline.embed_all_calls == 1


def test_next_capture_honors_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = _FakePipeline(all_faces=[_FakeFace(tuple(0.2 for _ in range(_DIM)), 0.9, 120)])
    backend = _backend(pipeline)

    fetched: list[int] = []

    def _fetch() -> bytes:
        fetched.append(1)
        return b"jpeg-frame"

    monkeypatch.setattr(backend, "_fetch_snapshot", _fetch)

    backend.set_capturing(False)
    assert asyncio.run(backend.next_capture()) is None
    # E-6: no frame is fetched from the source while capture is disabled.
    assert fetched == []
    assert pipeline.embed_all_calls == 0

    backend.set_capturing(True)
    capture = asyncio.run(backend.next_capture())
    assert capture is not None
    assert fetched == [1]


def test_backend_shares_pipeline_from_embedder() -> None:
    """When no pipeline is injected, the backend reuses the embedder's."""
    shared: Any = _FakePipeline(all_faces=[])
    embedder = HailoEmbedder(dim=_DIM, model_id=PINNED_MODEL_ID, pipeline=shared)
    backend = HardwareBackend(
        mode="hardware",
        embedder=embedder,
        snapshot_url="http://127.0.0.1:8082/snapshot",
        interval_ms=0,
    )
    assert backend._get_pipeline() is shared


def test_backend_close_closes_pipeline() -> None:
    pipeline = _FakePipeline(all_faces=[])
    backend = _backend(pipeline)
    asyncio.run(backend.close())
    assert pipeline.closed is True


def test_backend_status_reports_hardware_mode() -> None:
    backend = HardwareBackend(
        mode="single-camera",
        embedder=MockEmbedder(dim=_DIM),
        snapshot_url="http://x/snapshot",
        pipeline=_fake(all_faces=[]),
        interval_ms=33,
    )
    status = backend.status()
    assert status.mode == "single-camera"
    assert status.hailo_ok is True
    backend.set_capturing(False)
    assert backend.status().hailo_ok is False


# ---------------------------------------------------------------------------
# compat.probe_hailo
# ---------------------------------------------------------------------------


def test_probe_hailo_reports_model_id_and_dim(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_runtime = types.ModuleType("hailo_platform")
    fake_runtime.__version__ = PINNED_HAILO_RUNTIME  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hailo_platform", fake_runtime)
    monkeypatch.setattr(compat, "_recognizer_output_dim", lambda _path: PINNED_MODEL_DIM)
    reset_settings()

    detected = compat.probe_hailo()

    assert detected.runtime_version == PINNED_HAILO_RUNTIME
    assert detected.model_id == PINNED_MODEL_ID
    assert detected.model_dim == PINNED_MODEL_DIM

    result = check_compatibility(
        mode="hardware",
        expected_runtime=PINNED_HAILO_RUNTIME,
        expected_model_id=PINNED_MODEL_ID,
        expected_dim=PINNED_MODEL_DIM,
        detected=detected,
    )
    assert result.ok


def test_probe_hailo_model_unreadable_reports_no_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime present but the recognizer HEF unreadable -> model metadata None."""
    fake_runtime = types.ModuleType("hailo_platform")
    fake_runtime.__version__ = PINNED_HAILO_RUNTIME  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hailo_platform", fake_runtime)
    monkeypatch.setattr(compat, "_recognizer_output_dim", lambda _path: None)
    reset_settings()

    detected = compat.probe_hailo()

    assert detected.runtime_version == PINNED_HAILO_RUNTIME
    assert detected.model_id is None
    assert detected.model_dim is None


def test_probe_hailo_absent_returns_all_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure the import fails even if a prior test left a stub behind.
    monkeypatch.delitem(sys.modules, "hailo_platform", raising=False)
    reset_settings()

    detected = compat.probe_hailo()

    assert detected.runtime_version is None
    assert detected.model_id is None
    assert detected.model_dim is None
