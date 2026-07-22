"""Vision pipeline core + capture backends.

The privacy-critical logic lives in :class:`PipelineCore` and is identical in
every mode (mock/single-camera/dual-camera/hardware): detect faces already
carry embeddings from the backend; the core matches against the in-memory
enrolled set, applies the stability filter and per-person cooldown, refreshes
the ``current_visitor`` cache, and emits contract events.  For an *unknown*
face the only outputs are ``vision.face_visible`` (counts + pixel size) and
metric counters — the embedding is dropped at frame scope end and nothing is
persisted (ADR-0009 E-1).

The hardware seam is :class:`VisionBackend`: it produces per-frame
:class:`FrameCapture` objects.  ``ScriptedBackend`` is hardware-free and drives
mock/CI/simulator runs (and the sentinel privacy tests); ``HardwareBackend``
wraps the Hailo path and is only constructed after the compat check passes.
"""

from __future__ import annotations

import asyncio
import collections
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from doorboard_contracts.events import DoorboardEvent
from doorboard_observability import metrics as obs_metrics
from doorboard_observability.percentiles import summary

from door_visiond._uuid7 import uuid7
from door_visiond.clock import Clock
from door_visiond.embedder import Embedder
from door_visiond.embedding import Embedding
from door_visiond.events import (
    make_face_visible,
    make_identity_expired,
    make_identity_stable,
)
from door_visiond.identity_cache import CurrentVisitor, IdentityCache
from door_visiond.logging_setup import get_logger
from door_visiond.matcher import Matcher, MatchResult

if TYPE_CHECKING:
    from door_visiond.hailo_pipeline import HailoFacePipeline

logger = get_logger("door_visiond.pipeline")

EventSink = Callable[[DoorboardEvent], None]
CacheUpdateSink = Callable[[CurrentVisitor, str, UUID], None]
CacheClearSink = Callable[[CurrentVisitor, str, UUID], None]

_MAX_SAMPLES = 2000


# ---------------------------------------------------------------------------
# Backend adapter surface (the hardware seam)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectedFace:
    size_px: int
    embedding: Embedding
    quality: float = 1.0


@dataclass(frozen=True)
class FrameCapture:
    faces: tuple[DetectedFace, ...]
    inference_ms: float = 0.0
    dropped: int = 0


@dataclass(frozen=True)
class BackendStatus:
    mode: str
    hailo_ok: bool
    fps: float
    inference_ms_p50: float


class VisionBackend(Protocol):
    def set_capturing(self, enabled: bool) -> None:
        """Enable/disable capture at the frame source (privacy kill switch, E-6)."""
        ...

    async def next_capture(self) -> FrameCapture | None:
        """Return the next frame, or None if not capturing / no frame available."""
        ...

    def status(self) -> BackendStatus: ...

    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Pipeline core
# ---------------------------------------------------------------------------


@dataclass
class _Metrics:
    frame_count: int = 0
    face_visible_count: int = 0
    identity_stable_count: int = 0
    identity_expired_count: int = 0
    frame_drops: int = 0
    inference_ms: collections.deque[float] = field(
        default_factory=lambda: collections.deque(maxlen=_MAX_SAMPLES)
    )
    face_to_identity_ms: collections.deque[float] = field(
        default_factory=lambda: collections.deque(maxlen=_MAX_SAMPLES)
    )
    frame_ticks_ms: collections.deque[int] = field(
        default_factory=lambda: collections.deque(maxlen=64)
    )


class PipelineCore:
    """Mode-independent recognition logic; the single home of the privacy rules."""

    def __init__(
        self,
        *,
        matcher: Matcher,
        cache: IdentityCache,
        sink: EventSink,
        clock: Clock,
        door_id: str,
        min_face_px: int,
        ttl_ms: int,
        cooldown_ms: int,
        stability_window: int,
        stability_required: int,
        cache_update_sink: CacheUpdateSink | None = None,
        cache_clear_sink: CacheClearSink | None = None,
    ) -> None:
        self._matcher = matcher
        self._cache = cache
        self._sink = sink
        self._clock = clock
        self._door_id = door_id
        self._min_face_px = min_face_px
        self._ttl_ms = ttl_ms
        self._cooldown_ms = cooldown_ms
        self._stability_required = stability_required
        self._cache_update_sink = cache_update_sink
        self._cache_clear_sink = cache_clear_sink
        self._ring: collections.deque[str | None] = collections.deque(maxlen=stability_window)
        # Cooldown + streak bookkeeping, keyed only by ENROLLED person_id
        # (never by an unknown) and pruned on refresh / when a person leaves.
        self._last_stable_ms: dict[str, int] = {}
        self._first_seen_ms: dict[str, int] = {}
        self._streak_trace: dict[str, UUID] = {}
        self._metrics = _Metrics()

    # -- metrics accessors --------------------------------------------------

    @property
    def frame_count(self) -> int:
        return self._metrics.frame_count

    def metrics_snapshot(self) -> dict[str, float]:
        m = self._metrics
        inf = summary(list(m.inference_ms)) if m.inference_ms else {}
        f2i = summary(list(m.face_to_identity_ms)) if m.face_to_identity_ms else {}
        return {
            "frame_count": float(m.frame_count),
            "face_visible_count": float(m.face_visible_count),
            "identity_stable_count": float(m.identity_stable_count),
            "identity_expired_count": float(m.identity_expired_count),
            "frame_drops": float(m.frame_drops),
            "fps": self._fps(),
            "inference_ms_p50": inf.get("p50_ms", 0.0),
            "inference_ms_p95": inf.get("p95_ms", 0.0),
            "face_to_identity_ms_p50": f2i.get("p50_ms", 0.0),
            "face_to_identity_ms_p95": f2i.get("p95_ms", 0.0),
        }

    def inference_ms_p50(self) -> float:
        if not self._metrics.inference_ms:
            return 0.0
        return summary(list(self._metrics.inference_ms)).get("p50_ms", 0.0)

    def _fps(self) -> float:
        ticks = self._metrics.frame_ticks_ms
        if len(ticks) < 2:
            return 0.0
        elapsed_ms = ticks[-1] - ticks[0]
        if elapsed_ms <= 0:
            return 0.0
        return (len(ticks) - 1) * 1000.0 / elapsed_ms

    def on_matcher_refreshed(self, valid_person_ids: set[str]) -> None:
        """Drop cooldown/streak state for people no longer enrolled (bounded growth)."""
        for book in (self._last_stable_ms, self._first_seen_ms, self._streak_trace):
            for pid in [p for p in book if p not in valid_person_ids]:
                del book[pid]

    # -- frame processing ---------------------------------------------------

    def process_capture(self, capture: FrameCapture) -> None:
        """Process one frame. Unknown embeddings are matched then dropped here."""
        now = self._clock.monotonic_ms()
        m = self._metrics
        m.frame_count += 1
        m.frame_ticks_ms.append(now)
        if capture.inference_ms:
            m.inference_ms.append(capture.inference_ms)
        if capture.dropped:
            m.frame_drops += capture.dropped

        if capture.faces:
            m.face_visible_count += 1
            largest_px = max(f.size_px for f in capture.faces)
            self._sink(
                make_face_visible(
                    clock=self._clock,
                    door_id=self._door_id,
                    trace_id=uuid7(),
                    face_count=len(capture.faces),
                    largest_face_px=largest_px,
                )
            )

        result = self._match_primary_face(capture, now)
        self._ring.append(result.person_id if result is not None else None)
        self._prune_departed()

        if result is not None:
            self._maybe_emit_stable(result, now)
        # NOTE: the embedding referenced above goes out of scope here. It is
        # never stored, buffered, logged, or keyed by identity (E-1).

    def _match_primary_face(self, capture: FrameCapture, now: int) -> MatchResult | None:
        # The primary face is the largest one meeting the minimum size gate.
        qualifying = [f for f in capture.faces if f.size_px >= self._min_face_px]
        if not qualifying:
            return None
        primary = max(qualifying, key=lambda f: f.size_px)
        result = self._matcher.match(primary.embedding)
        if result is None:
            return None
        # First appearance in this streak → record first-seen for the metric.
        if result.person_id not in self._first_seen_ms:
            self._first_seen_ms[result.person_id] = now
            self._streak_trace[result.person_id] = uuid7()
        return result

    def _maybe_emit_stable(self, result: MatchResult, now: int) -> None:
        person_id = result.person_id
        # Stability: same person matched >= required times in the sliding window.
        if self._ring.count(person_id) < self._stability_required:
            return

        expires_mono = now + self._ttl_ms
        expires_utc = self._clock.utc_now() + timedelta(milliseconds=self._ttl_ms)

        # Always refresh the cache so the button gets a fresh personalization,
        # independent of the (longer) greeting cooldown.
        prior = self._cache.current(now)
        priority = (
            "high"
            if prior is None
            or prior.person_id != person_id
            or prior.profile_id != result.profile_id
            else "normal"
        )
        visitor = CurrentVisitor(
            person_id=person_id,
            display_name=result.display_name,
            profile_id=result.profile_id,
            expires_at_monotonic_ms=expires_mono,
            expires_at_utc=expires_utc,
        )
        self._cache.set(visitor)
        if self._cache_update_sink is not None:
            trace = self._streak_trace.get(person_id, uuid7())
            self._cache_update_sink(visitor, priority, trace)

        last = self._last_stable_ms.get(person_id)
        if last is not None and now - last < self._cooldown_ms:
            return  # greeting cooldown (P-10): at most one identity_stable / 30 s

        trace = self._streak_trace.get(person_id, uuid7())
        first_seen = self._first_seen_ms.get(person_id, now)
        self._metrics.face_to_identity_ms.append(float(now - first_seen))
        obs_metrics.record_sample("face_to_stable_identity", float(now - first_seen))

        self._sink(
            make_identity_stable(
                clock=self._clock,
                door_id=self._door_id,
                trace_id=trace,
                person_id=person_id,
                display_name=result.display_name,
                confidence=result.score,
                expires_at=expires_utc,
                expires_at_monotonic_ms=expires_mono,
                profile_id=result.profile_id,
            )
        )
        self._last_stable_ms[person_id] = now
        self._metrics.identity_stable_count += 1

    def _prune_departed(self) -> None:
        present = {pid for pid in self._ring if pid is not None}
        for pid in [p for p in self._first_seen_ms if p not in present]:
            del self._first_seen_ms[pid]
            self._streak_trace.pop(pid, None)

    def tick(self) -> None:
        """Expire the cache if its TTL elapsed and emit identity_expired."""
        expired = self._cache.expire_if_due(self._clock.monotonic_ms())
        if expired is not None:
            self._emit_expired(expired, reason="expired")

    def clear_cache_and_notify(self, *, reason: str = "admin") -> None:
        """Force-clear the cache (privacy mode / unenroll) and emit identity_expired."""
        prior = self._cache.clear()
        if prior is not None:
            self._emit_expired(prior, reason=reason)

    def _emit_expired(self, visitor: CurrentVisitor, *, reason: str) -> None:
        person_id = visitor.person_id
        trace = self._streak_trace.pop(person_id, None) or uuid7()
        self._first_seen_ms.pop(person_id, None)
        self._sink(
            make_identity_expired(
                clock=self._clock,
                door_id=self._door_id,
                trace_id=trace,
                person_id=person_id,
            )
        )
        if self._cache_clear_sink is not None:
            self._cache_clear_sink(visitor, reason, trace)
        self._metrics.identity_expired_count += 1


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class DisabledBackend:
    """No capture at all — generic-greeting mode (Hailo absent / privacy / off)."""

    def __init__(self, *, interval_ms: int = 100) -> None:
        self._interval_s = interval_ms / 1000.0

    def set_capturing(self, enabled: bool) -> None:  # noqa: ARG002 - always off
        return

    async def next_capture(self) -> FrameCapture | None:
        await asyncio.sleep(self._interval_s)
        return None

    def status(self) -> BackendStatus:
        return BackendStatus(mode="disabled", hailo_ok=False, fps=0.0, inference_ms_p50=0.0)

    async def close(self) -> None:
        return


class ScriptedBackend:
    """Hardware-free backend that replays a script of frames (mock/CI/simulator)."""

    def __init__(
        self,
        captures: Sequence[FrameCapture],
        *,
        mode: str = "mock",
        interval_ms: int = 100,
        loop: bool = True,
    ) -> None:
        self._script = list(captures)
        self._mode = mode
        self._interval_s = interval_ms / 1000.0
        self._loop = loop
        self._idx = 0
        self._capturing = True
        self._last_inference_ms = 5.0

    def set_capturing(self, enabled: bool) -> None:
        self._capturing = enabled

    async def next_capture(self) -> FrameCapture | None:
        await asyncio.sleep(self._interval_s)
        if not self._capturing:
            return None
        if not self._script:
            return FrameCapture(faces=(), inference_ms=self._last_inference_ms)
        if self._idx >= len(self._script):
            if not self._loop:
                return None
            self._idx = 0
        capture = self._script[self._idx]
        self._idx += 1
        if capture.inference_ms:
            self._last_inference_ms = capture.inference_ms
        return capture

    def status(self) -> BackendStatus:
        return BackendStatus(
            mode=self._mode,
            hailo_ok=self._mode != "disabled",
            fps=1.0 / self._interval_s if self._interval_s else 0.0,
            inference_ms_p50=self._last_inference_ms,
        )

    async def close(self) -> None:
        return


class HardwareBackend:
    """Hailo-backed capture (single/dual-camera/hardware).

    door-media owns the camera; this backend pulls a still from door-media's
    HTTP snapshot endpoint (never opening the camera itself), then runs the
    shared :class:`~door_visiond.hailo_pipeline.HailoFacePipeline` to detect all
    faces and embed each.  The resulting :class:`FrameCapture` feeds the same
    unchanged :class:`PipelineCore`.  Blocking HTTP + inference run in a worker
    thread so the asyncio run loop is never stalled.

    Privacy kill switch (E-6): while ``set_capturing(False)`` is in effect, no
    snapshot is fetched and ``next_capture`` returns ``None`` — capture stops at
    the source.
    """

    def __init__(
        self,
        *,
        mode: str,
        embedder: Embedder,
        snapshot_url: str,
        snapshot_timeout_s: float = 2.0,
        pipeline: HailoFacePipeline | None = None,
        interval_ms: int = 33,
    ) -> None:
        self._mode = mode
        self._embedder = embedder
        self._snapshot_url = snapshot_url
        self._snapshot_timeout_s = snapshot_timeout_s
        self._pipeline = pipeline
        self._interval_s = interval_ms / 1000.0
        self._capturing = True
        self._last_inference_ms = 0.0

    def set_capturing(self, enabled: bool) -> None:
        self._capturing = enabled

    def _get_pipeline(self) -> HailoFacePipeline:
        if self._pipeline is None:
            from door_visiond.embedder import HailoEmbedder

            if not isinstance(self._embedder, HailoEmbedder):
                msg = "HardwareBackend needs a HailoFacePipeline or a HailoEmbedder"
                raise RuntimeError(msg)
            self._pipeline = self._embedder.pipeline
        return self._pipeline

    def _fetch_snapshot(self) -> bytes:
        import urllib.request

        request = urllib.request.Request(self._snapshot_url, method="GET")  # noqa: S310
        with urllib.request.urlopen(  # noqa: S310
            request, timeout=self._snapshot_timeout_s
        ) as response:
            return response.read()

    def _capture_blocking(self) -> FrameCapture | None:
        if not self._capturing:
            return None
        image_bytes = self._fetch_snapshot()
        faces, inference_ms = self._get_pipeline().embed_all(image_bytes)
        # Re-check the kill switch: do not surface faces captured mid-toggle.
        if not self._capturing:
            return None
        self._last_inference_ms = inference_ms
        detected = tuple(
            DetectedFace(
                size_px=face.size_px,
                embedding=Embedding(face.vector),
                quality=face.score,
            )
            for face in faces
        )
        return FrameCapture(faces=detected, inference_ms=inference_ms)

    async def next_capture(self) -> FrameCapture | None:
        await asyncio.sleep(self._interval_s)
        if not self._capturing:
            return None
        return await asyncio.to_thread(self._capture_blocking)

    def status(self) -> BackendStatus:
        return BackendStatus(
            mode=self._mode,
            hailo_ok=self._capturing,
            fps=1.0 / self._interval_s if self._interval_s else 0.0,
            inference_ms_p50=self._last_inference_ms,
        )

    async def close(self) -> None:
        if self._pipeline is not None:
            await asyncio.to_thread(self._pipeline.close)


def default_mock_script(dim: int) -> list[FrameCapture]:
    """A benign default for a running mock service: one small unknown face.

    Produces ``face_visible`` traffic without matching anyone (no enrollment
    assumed).  Tests supply their own scripts.
    """
    from door_visiond.embedder import MockEmbedder

    emb, _ = MockEmbedder(dim=dim).embed(b"mock-unknown-visitor")
    return [FrameCapture(faces=(DetectedFace(size_px=120, embedding=emb),), inference_ms=5.0)]
