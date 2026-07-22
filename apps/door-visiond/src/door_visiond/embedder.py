"""Embedder adapter: raw face image bytes -> ``Embedding`` + quality score.

This is a hardware seam.  ``MockEmbedder`` is deterministic and hardware-free
(dev/CI); ``HailoEmbedder`` wraps the Hailo runtime and is only constructed in
``single-camera``/``dual-camera``/``hardware`` modes after the startup
compatibility check passes.  The official Pi/Hailo face-recognition material is
a *reference*: any real implementation lives behind this interface and must not
couple the pipeline to a demo's structure (handoff §10).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Protocol

from door_visiond.embedding import Embedding

if TYPE_CHECKING:
    from door_visiond.hailo_pipeline import HailoFacePipeline


class Embedder(Protocol):
    def embed(self, image_bytes: bytes) -> tuple[Embedding, float]:
        """Return (embedding, quality in [0,1]) for a single aligned face image."""
        ...

    @property
    def model_id(self) -> str: ...

    @property
    def dim(self) -> int: ...


def sentinel_embedding(sentinel: bytes, dim: int) -> Embedding:
    """Build an Embedding whose float32 bytes begin with *sentinel*.

    Used by the sentinel-based privacy tests (ADR-0009 §7): the marker survives
    into any serialization of the vector, so a byte-scan of disk/logs proves the
    embedding never leaked.  *sentinel* must be ASCII and a multiple of 4 bytes
    so the float32 round-trip is bit-exact (no NaN payload canonicalization).
    """
    buf = bytearray(dim * 4)
    buf[: len(sentinel)] = sentinel[: dim * 4]
    return Embedding.from_le_float32_bytes(bytes(buf))


class MockEmbedder:
    """Deterministic, hardware-free embedder for dev/CI/simulator.

    The same image bytes always yield the same vector (so enrollment then
    recognition of the same source matches).  When constructed with a
    ``sentinel``, every embedding carries that marker for the privacy tests.
    """

    def __init__(
        self,
        *,
        dim: int = 512,
        model_id: str = "mock-embedder-v1",
        sentinel: bytes | None = None,
        min_quality_bytes: int = 8,
    ) -> None:
        self._dim = dim
        self._model_id = model_id
        self._sentinel = sentinel
        self._min_quality_bytes = min_quality_bytes

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, image_bytes: bytes) -> tuple[Embedding, float]:
        quality = 0.9 if len(image_bytes) >= self._min_quality_bytes else 0.1
        if self._sentinel is not None:
            return sentinel_embedding(self._sentinel, self._dim), quality
        return self._deterministic(image_bytes), quality

    def _deterministic(self, image_bytes: bytes) -> Embedding:
        # Expand a hash into `dim` floats in [-1, 1]; stable per input, never NaN.
        values: list[float] = []
        counter = 0
        while len(values) < self._dim:
            digest = hashlib.sha256(image_bytes + counter.to_bytes(4, "big")).digest()
            for byte in digest:
                values.append((byte / 127.5) - 1.0)
                if len(values) >= self._dim:
                    break
            counter += 1
        return Embedding(tuple(values))


class HailoEmbedder:
    """Hardware embedder: detect -> pick largest face -> align -> ArcFace embed.

    Delegates to a shared :class:`~door_visiond.hailo_pipeline.HailoFacePipeline`
    (T-305) so the VDevice + models are opened once and reused across enrollment
    stills and live frames.  Constructed only after the startup compat check
    passes.  ``hailo_platform``/``cv2`` are imported lazily by the pipeline, so
    importing this module stays safe in mock/CI environments.
    """

    def __init__(
        self,
        *,
        dim: int,
        model_id: str,
        pipeline: HailoFacePipeline | None = None,
        detector_hef_path: str | None = None,
        recognizer_hef_path: str | None = None,
    ) -> None:
        self._dim = dim
        self._model_id = model_id
        self._pipeline = pipeline
        self._detector_hef_path = detector_hef_path
        self._recognizer_hef_path = recognizer_hef_path

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def pipeline(self) -> HailoFacePipeline:
        """The shared face pipeline (built lazily from HEF paths if needed)."""
        if self._pipeline is None:
            if self._detector_hef_path is None or self._recognizer_hef_path is None:
                msg = "HailoEmbedder has neither a pipeline nor HEF paths configured"
                raise RuntimeError(msg)
            from door_visiond.hailo_pipeline import HailoFacePipeline

            self._pipeline = HailoFacePipeline(
                detector_hef_path=self._detector_hef_path,
                recognizer_hef_path=self._recognizer_hef_path,
                model_id=self._model_id,
                dim=self._dim,
            )
        return self._pipeline

    def embed(self, image_bytes: bytes) -> tuple[Embedding, float]:
        face = self.pipeline.embed_primary(image_bytes)
        if face is None:
            # No face detected: return a placeholder zero vector with a quality
            # below min_enroll_quality so enrollment rejects it (never enrolls a
            # face-less image).
            return Embedding(tuple([0.0] * self._dim)), 0.0
        return Embedding(face.vector), face.score
