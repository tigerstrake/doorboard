"""doorboard-observability — shared telemetry helpers.

Exports:
    percentiles: pure percentile math (p50/p95/p99/summary)
    metrics: Prometheus histogram helpers + in-memory sample store
    clock_offset: cross-device clock-offset estimation (ESP32 ↔ Pi)
"""

from doorboard_observability import clock_offset, metrics, percentiles
from doorboard_observability.clock_offset import ClockOffsetEstimator, OffsetSample
from doorboard_observability.metrics import LATENCY_PATHS, measure, record_sample
from doorboard_observability.percentiles import p50, p95, p99, percentile, summary

__all__ = [
    "ClockOffsetEstimator",
    "LATENCY_PATHS",
    "OffsetSample",
    "clock_offset",
    "measure",
    "metrics",
    "p50",
    "p95",
    "p99",
    "percentile",
    "percentiles",
    "record_sample",
    "summary",
]
