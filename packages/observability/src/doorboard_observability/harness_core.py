"""Shared logic for the latency harness: paths, budgets, regression check, reports.

This module is part of doorboard-observability (installed as an editable
package) so it is importable anywhere in the workspace — including from
tests/performance/test_harness.py — without any sys.path manipulation.

Tests import from here; the CLI runner (tests/performance/harness.py)
also imports from here and provides the argparse entrypoint.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from doorboard_observability.percentiles import p50, p95, p99

# ---------------------------------------------------------------------------
# Named paths and budgets (ARCHITECTURE.md §4)
# ---------------------------------------------------------------------------

# Regression threshold: a p95 that is more than this many times the baseline
# is a regression.  3× catches order-of-magnitude regressions while ignoring
# measurement noise.
REGRESSION_FACTOR: float = 3.0

# Canonical path names → p95 budget in milliseconds.
# Keep in sync with ARCHITECTURE.md §4.
BUDGET_P95_MS: dict[str, float] = {
    "button_to_generic_feedback": 30.0,
    "button_to_personalized_feedback": 100.0,
    "tap_to_local_response": 100.0,
    "face_to_stable_identity": 600.0,
    "bell_to_visitor_mode": 250.0,
    "bell_to_recording_event": 500.0,
    "webrtc_glass_to_glass": 750.0,
}

# Paths the harness cannot time on a machine without the door, and why. These are
# reported as N/A by name: a number for any of them could only ever read "pass",
# and a budget that cannot fail is worse than one marked unmeasured. Where the
# path is verified instead is part of the reason.
SIMULATOR_NA: dict[str, str] = {
    "button_to_generic_feedback": "ESP32-local (ISR → LED/audio), never reaches the Pi; bench only",
    "button_to_personalized_feedback": "ESP32-local (cached profile → effect); bench only",
    "tap_to_local_response": "browser-side (pointerdown → first paint on /doorpad); bench only",
    "face_to_stable_identity": (
        "needs a camera and the Hailo; on the door: door-visiond's face_to_identity_ms_p95"
    ),
    "webrtc_glass_to_glass": "needs MediaMTX and a browser; bench only",
}

# The paths the harness does time, through the real service code in-process.
MEASURED_PATHS: tuple[str, ...] = tuple(p for p in BUDGET_P95_MS if p not in SIMULATOR_NA)


def _is_placeholder(vals: list[float]) -> bool:
    """True when a path has only sentinel 0.0 values (the pre-name-based N/A marker)."""
    return bool(vals) and all(v == 0.0 for v in vals)


def _is_na(path: str, vals: list[float]) -> bool:
    return path in SIMULATOR_NA or _is_placeholder(vals)


def _na_reason(path: str) -> str:
    return SIMULATOR_NA.get(path, "cannot be timed off the door")


def _ordered_paths(samples: dict[str, list[float]]) -> list[str]:
    """Every §4 path in table order, then any extra path the samples carry."""
    extras = sorted(p for p in samples if p not in BUDGET_P95_MS)
    return [*BUDGET_P95_MS, *extras]


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def build_json(
    samples: dict[str, list[float]],
    regressions: list[str],
) -> dict[str, Any]:
    """Return the full JSON report structure.

    Every N/A path is present with ``simulator_na`` and its reason, samples or
    not; a measured path with no samples is left out (it was not run).
    """
    paths: dict[str, Any] = {}
    for path in _ordered_paths(samples):
        vals = samples.get(path, [])
        if _is_na(path, vals):
            paths[path] = {"simulator_na": True, "reason": _na_reason(path)}
            continue
        if not vals:
            continue
        paths[path] = {
            "p50_ms": p50(vals),
            "p95_ms": p95(vals),
            "p99_ms": p99(vals),
            "min_ms": min(vals),
            "max_ms": max(vals),
            "count": len(vals),
            "budget_p95_ms": BUDGET_P95_MS.get(path),
        }

    return {
        "regressions": regressions,
        "regression_factor": REGRESSION_FACTOR,
        "paths": paths,
    }


def build_report(
    samples: dict[str, list[float]],
    *,
    baseline: dict[str, Any] | None,
    regressions: list[str],
) -> str:
    """Return a human-readable latency table."""
    lines: list[str] = [
        "═" * 78,
        "  Doorboard latency harness — in-process run (real service code, no hardware)",
        "═" * 78,
        f"  {'PATH':<40} {'P50':>7} {'P95':>7} {'P99':>7}  {'BUDGET':>8}  STATUS",
        "─" * 78,
    ]
    not_measured: list[str] = []

    for path in _ordered_paths(samples):
        vals = samples.get(path, [])
        if _is_na(path, vals):
            lines.append(f"  {path:<40} {'N/A':>7} {'N/A':>7} {'N/A':>7}  {'—':>8}  simulator N/A")
            not_measured.append(path)
            continue
        if not vals:
            continue

        vp50 = p50(vals)
        vp95 = p95(vals)
        vp99 = p99(vals)
        budget = BUDGET_P95_MS.get(path)
        budget_str = f"{budget:.0f} ms" if budget is not None else "—"

        # Regression entries are messages, "<path>: current p95=…", not bare names.
        if any(r.startswith(f"{path}:") for r in regressions):
            status = "❌ REGRESSION"
        elif budget is not None and vp95 > budget:
            status = "⚠ over budget"
        else:
            status = "✓ measured"

        lines.append(
            f"  {path:<40} {vp50:>6.1f}ms {vp95:>6.1f}ms {vp99:>6.1f}ms  {budget_str:>8}  {status}"
        )

    lines.append("─" * 78)

    if not_measured:
        lines.append("\n  Not measured here (simulator N/A):")
        for path in not_measured:
            lines.append(f"     • {path}: {_na_reason(path)}")

    if regressions:
        lines.append(f"\n  ⛔ {len(regressions)} regression(s) detected vs baseline:")
        for r in regressions:
            lines.append(f"     • {r}")
        lines.append(f"\n  Regression threshold: {REGRESSION_FACTOR}× the baseline p95.")
    elif baseline is not None:
        lines.append("\n  ✓ No regressions vs baseline.")
    else:
        lines.append("\n  (No baseline file — run with --save-baseline to create one.)")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------


def check_regressions(
    samples: dict[str, list[float]],
    baseline: dict[str, Any],
) -> list[str]:
    """Return list of path names where p95 exceeds baseline_p95 × REGRESSION_FACTOR.

    Rules:
    - Empty sample lists are skipped (not a regression).
    - N/A paths (by name, or the all-zero sentinel) are skipped.
    - Paths absent from the baseline are skipped (new paths don't regress).
    - Baseline entries missing p95_ms are skipped (malformed, not a crash).
    """
    regressions: list[str] = []
    baseline_paths: dict[str, Any] = baseline.get("paths", {})

    for path, vals in samples.items():
        if not vals or _is_na(path, vals):
            continue
        base = baseline_paths.get(path)
        if base is None or base.get("simulator_na"):
            continue
        base_p95_raw = base.get("p95_ms")
        if base_p95_raw is None:
            continue
        base_p95: float = float(base_p95_raw)
        current_p95 = p95(vals)
        threshold = max(base_p95, 1.0) * REGRESSION_FACTOR
        if current_p95 > threshold:
            regressions.append(
                f"{path}: current p95={current_p95:.1f}ms, "
                f"baseline p95={base_p95:.1f}ms, "
                f"threshold={threshold:.1f}ms"
            )

    return regressions


def load_baseline(path: Path) -> dict[str, Any] | None:
    """Load a baseline JSON file.  Returns None on missing file; raises on parse error."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def save_baseline(
    path: Path,
    samples: dict[str, list[float]],
    regressions: list[str],
) -> None:
    """Write the current run as the new baseline."""
    report = build_json(samples, regressions)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
