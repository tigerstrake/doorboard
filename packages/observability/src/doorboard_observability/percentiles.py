"""Percentile computation utilities.

All sample values are in milliseconds (floats) measured with monotonic clocks.
Functions are pure and dependency-free so they are usable in any service or test.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def percentile(samples: Sequence[float], p: float) -> float:
    """Return the p-th percentile of *samples* using the nearest-rank method.

    Args:
        samples: A non-empty sequence of float values (milliseconds).
        p: Percentile in the range [0, 100].

    Returns:
        The value at the p-th percentile.

    Raises:
        ValueError: If *samples* is empty or *p* is out of range.
    """
    if not samples:
        msg = "samples must be non-empty"
        raise ValueError(msg)
    if not (0 <= p <= 100):
        msg = f"p must be in [0, 100], got {p}"
        raise ValueError(msg)
    sorted_samples = sorted(samples)
    n = len(sorted_samples)
    # Nearest-rank: index = ceil(p/100 * n) - 1, clamped to [0, n-1]
    idx = max(0, math.ceil(p / 100.0 * n) - 1)
    return sorted_samples[idx]


def p50(samples: Sequence[float]) -> float:
    """Return the 50th percentile (median) of *samples*."""
    return percentile(samples, 50)


def p95(samples: Sequence[float]) -> float:
    """Return the 95th percentile of *samples*."""
    return percentile(samples, 95)


def p99(samples: Sequence[float]) -> float:
    """Return the 99th percentile of *samples*."""
    return percentile(samples, 99)


def summary(samples: Sequence[float]) -> dict[str, float]:
    """Return a dict with p50/p95/p99/min/max/count for *samples*.

    Raises:
        ValueError: If *samples* is empty.
    """
    if not samples:
        msg = "samples must be non-empty"
        raise ValueError(msg)
    return {
        "p50_ms": p50(samples),
        "p95_ms": p95(samples),
        "p99_ms": p99(samples),
        "min_ms": min(samples),
        "max_ms": max(samples),
        "count": float(len(samples)),
    }
