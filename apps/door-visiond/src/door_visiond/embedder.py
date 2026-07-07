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
from typing import Protocol

from door_visiond.embedding import Embedding


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
    """Hardware embedder placeholder — never instantiated without a Hailo device.

    Real detect/align/embed is deferred to hardware bring-up (T-302 acceptance:
    hardware paths behind this adapter).  Constructing/using it in a
    hardware-absent environment fails loudly rather than pretending.
    """

    def __init__(self, *, dim: int, model_id: str) -> None:
        self._dim = dim
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, image_bytes: bytes) -> tuple[Embedding, float]:
        msg = "HailoEmbedder requires the Hailo runtime; unavailable in this environment"
        raise RuntimeError(msg)
