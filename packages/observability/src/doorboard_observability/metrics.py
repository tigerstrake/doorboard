"""Prometheus histogram helpers for every ARCHITECTURE.md §4 latency path.

Usage (in any FastAPI service)::

    from doorboard_observability.metrics import record_sample, LATENCY_PATHS

    # Record a single millisecond sample for the named path:
    record_sample("button_to_generic_feedback", duration_ms)

    # Mount Prometheus metrics on your FastAPI app:
    from prometheus_client import make_asgi_app
    app.mount("/metrics", make_asgi_app())

The histograms are pre-declared with path-appropriate buckets so every service
uses identical label names and bucket boundaries. Services that cannot import
prometheus_client in test/mock mode call this module with PROMETHEUS_AVAILABLE
being False; functions still accumulate samples in-memory for the
system.latency_sample event emission path.
"""

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager
from typing import TYPE_CHECKING

from doorboard_observability.percentiles import summary

if TYPE_CHECKING:
    from collections.abc import Generator

# ---------------------------------------------------------------------------
# Named measurement points for each §4 path
# ---------------------------------------------------------------------------

# Canonical path names — used as histogram labels and in reports.
# Keep in sync with ARCHITECTURE.md §4.
LATENCY_PATHS: dict[str, dict[str, object]] = {
    # button → generic LED/audio (ESP32-local); p95 < 30 ms
    "button_to_generic_feedback": {
        "description": "Button press to generic LED/audio feedback (ESP32-local)",
        "budget_p95_ms": 30,
        "buckets": (1, 2, 5, 10, 15, 20, 25, 30, 50, 100),
    },
    # button → cached personalized effect; p95 < 100 ms
    "button_to_personalized_feedback": {
        "description": "Button press to personalized feedback (cached profile)",
        "budget_p95_ms": 100,
        "buckets": (5, 10, 20, 30, 50, 75, 100, 150, 200),
    },
    # touchscreen tap → visible local response; p95 < 100 ms
    "tap_to_local_response": {
        "description": "Touchscreen tap to visible local UI response",
        "budget_p95_ms": 100,
        "buckets": (5, 10, 20, 30, 50, 75, 100, 150, 200),
    },
    # face visible → stable identity; p95 < 600 ms
    "face_to_stable_identity": {
        "description": "Face first visible to stable identity match",
        "budget_p95_ms": 600,
        "buckets": (50, 100, 150, 200, 300, 400, 500, 600, 800, 1000),
    },
    # bell → visitor mode on large display; p95 < 250 ms
    "bell_to_visitor_mode": {
        "description": "Bell press to visitor mode active on wallboard",
        "budget_p95_ms": 250,
        "buckets": (10, 25, 50, 75, 100, 150, 200, 250, 350, 500),
    },
    # bell → recording event (stream already live); < 500 ms
    "bell_to_recording_event": {
        "description": "Bell press to recording started (stream pre-warmed)",
        "budget_p95_ms": 500,
        "buckets": (25, 50, 100, 150, 200, 300, 400, 500, 750, 1000),
    },
    # local live video (WebRTC); < 750 ms
    "webrtc_glass_to_glass": {
        "description": "WebRTC glass-to-glass live video latency",
        "budget_p95_ms": 750,
        "buckets": (50, 100, 150, 200, 300, 400, 500, 600, 750, 1000),
    },
}

# ---------------------------------------------------------------------------
# In-memory sample store (always active; used for system.latency_sample events)
# ---------------------------------------------------------------------------

_samples: dict[str, list[float]] = defaultdict(list)


def record_sample(path: str, duration_ms: float) -> None:
    """Record one millisecond sample for *path*.

    Also observes into the Prometheus histogram if prometheus_client is
    available.  The *path* must be a key in LATENCY_PATHS; unknown paths are
    silently accepted so that new paths added before this file is updated do
    not crash services.
    """
    _samples[path].append(duration_ms)
    _observe_prometheus(path, duration_ms)


@contextmanager
def measure(path: str) -> Generator[None, None, None]:
    """Context manager that records wall-clock elapsed time for *path*.

    Uses ``time.monotonic()`` — never ``datetime.now()``.

    Example::

        with measure("bell_to_visitor_mode"):
            await transition_to_visitor_mode()
    """
    t0 = time.monotonic()
    try:
        yield
    finally:
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        record_sample(path, elapsed_ms)


def get_samples(path: str) -> list[float]:
    """Return a copy of accumulated samples for *path* (milliseconds)."""
    return list(_samples[path])


def all_summaries() -> dict[str, dict[str, float]]:
    """Return percentile summaries for every path that has at least one sample."""
    return {path: summary(vals) for path, vals in _samples.items() if vals}


def reset_samples(path: str | None = None) -> None:
    """Clear accumulated samples. Pass *path=None* to clear all paths.

    Intended for test teardown and harness re-runs; not for production use.
    """
    if path is None:
        _samples.clear()
    else:
        _samples[path].clear()


# ---------------------------------------------------------------------------
# Prometheus integration (optional; gracefully absent in CI/mock mode)
# ---------------------------------------------------------------------------

PROMETHEUS_AVAILABLE: bool = False
_histograms: dict[str, object] = {}

try:
    from prometheus_client import Histogram  # type: ignore

    PROMETHEUS_AVAILABLE = True  # type: ignore

    for _path, _spec in LATENCY_PATHS.items():
        _histograms[_path] = Histogram(
            name=f"doorboard_latency_{_path}_ms",
            documentation=str(_spec["description"]),
            buckets=list(_spec["buckets"]) + [float("inf")],  # type: ignore[arg-type]
        )

except ImportError:  # pragma: no cover — optional dependency
    pass


def _observe_prometheus(path: str, duration_ms: float) -> None:
    if not PROMETHEUS_AVAILABLE:
        return
    h = _histograms.get(path)
    if h is not None:
        h.observe(duration_ms)  # type: ignore[union-attr]
