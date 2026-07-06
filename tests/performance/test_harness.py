"""Integration tests for the latency harness.

Imports shared logic from doorboard_observability.harness_core (installed
as an editable package), not from tests/performance/harness.py, so no
sys.path manipulation is needed.

Test coverage:
- check_regressions: intentionally slowed fixture MUST trip the check
  (acceptance criterion from the brief).
- No regression below the 3× threshold.
- Only the regressing path is flagged.
- Placeholder paths (webrtc simulator-N/A) never flagged.
- Unknown paths in samples, empty samples, malformed baseline — all safe.
- build_json / build_report produce well-formed output.
- Baseline round-trips correctly via load_baseline / save_baseline.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from doorboard_observability.harness_core import (
    BUDGET_P95_MS,
    REGRESSION_FACTOR,
    build_json,
    build_report,
    check_regressions,
    load_baseline,
    save_baseline,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_samples(*, multiplier: float = 1.0) -> dict[str, list[float]]:
    """Return synthetic samples for every path at 10% of budget × multiplier."""
    result: dict[str, list[float]] = {}
    for path, budget in BUDGET_P95_MS.items():
        if path == "webrtc_glass_to_glass":
            result[path] = [0.0] * 50  # sentinel
            continue
        val = budget * 0.10 * multiplier
        result[path] = [val] * 50
    return result


def _make_baseline(samples: dict[str, list[float]]) -> dict[str, Any]:
    return build_json(samples, [])


# ---------------------------------------------------------------------------
# check_regressions — acceptance-criterion tests
# ---------------------------------------------------------------------------


class TestRegressionDetection:
    def test_no_regression_on_identical_samples(self) -> None:
        samples = _make_samples()
        baseline = _make_baseline(samples)
        assert check_regressions(samples, baseline) == []

    def test_no_regression_below_threshold(self) -> None:
        """2× slowdown (< REGRESSION_FACTOR=3×) must NOT be flagged."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        slow = _make_samples(multiplier=2.0)
        assert check_regressions(slow, baseline) == []

    def test_regression_detected_above_threshold(self) -> None:
        """REGRESSION_FACTOR+1 × slowdown MUST be flagged — acceptance criterion.

        The brief requires: "intentionally slowed code path (test fixture) trips the check."
        """
        fast = _make_samples()
        baseline = _make_baseline(fast)
        # 4× slower → above REGRESSION_FACTOR (3×)
        very_slow = _make_samples(multiplier=REGRESSION_FACTOR + 1.0)
        regressions = check_regressions(very_slow, baseline)
        non_webrtc = [p for p in BUDGET_P95_MS if p != "webrtc_glass_to_glass"]
        for path in non_webrtc:
            assert any(path in r for r in regressions), (
                f"Expected regression for {path!r} not found in {regressions}"
            )

    def test_exactly_at_threshold_not_regression(self) -> None:
        """Exactly REGRESSION_FACTOR× is at the limit but must NOT be flagged
        (boundary: > not >=)."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        # Exactly REGRESSION_FACTOR× the baseline p95
        at_threshold = {}
        for path, _vals in fast.items():
            if path == "webrtc_glass_to_glass":
                at_threshold[path] = [0.0] * 50
            else:
                base_p95 = baseline["paths"][path]["p95_ms"]
                at_threshold[path] = [base_p95 * REGRESSION_FACTOR] * 50
        regressions = check_regressions(at_threshold, baseline)
        assert regressions == [], f"Should not flag at exactly threshold: {regressions}"

    def test_only_slow_path_flagged(self) -> None:
        """Only the one regressing path should appear in the regression list."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        target = "button_to_generic_feedback"
        base_p95 = baseline["paths"][target]["p95_ms"]

        regressing = dict(fast)
        regressing[target] = [base_p95 * (REGRESSION_FACTOR + 1.0)] * 50

        regressions = check_regressions(regressing, baseline)
        assert any(target in r for r in regressions), "Target must be flagged"
        other = [p for p in BUDGET_P95_MS if p != target and p != "webrtc_glass_to_glass"]
        for path in other:
            assert not any(path in r for r in regressions), f"{path!r} should NOT be a regression"

    def test_webrtc_placeholder_never_regresses(self) -> None:
        """Sentinel 0.0 path is always excluded from regression checks."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        current = dict(fast)
        current["webrtc_glass_to_glass"] = [999_999.0] * 50
        regressions = check_regressions(current, baseline)
        assert not any("webrtc_glass_to_glass" in r for r in regressions)

    def test_empty_samples_not_regression(self) -> None:
        """Empty sample list for a path must not be flagged and must not raise."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        current = dict(fast)
        current["button_to_generic_feedback"] = []
        regressions = check_regressions(current, baseline)
        assert not any("button_to_generic_feedback" in r for r in regressions)

    def test_unknown_path_in_samples_not_regression(self) -> None:
        """A path in samples that's absent from baseline must not raise or flag."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        extra = dict(fast)
        extra["future_unregistered_path"] = [999_999.0] * 50
        regressions = check_regressions(extra, baseline)
        assert not any("future_unregistered_path" in r for r in regressions)

    def test_empty_baseline_paths_no_regression(self) -> None:
        """baseline={'paths': {}} must return empty regression list."""
        fast = _make_samples()
        regressions = check_regressions(fast, {"paths": {}})
        assert regressions == []

    def test_malformed_baseline_entry_skipped(self) -> None:
        """Baseline entry missing p95_ms must be skipped, not crash."""
        fast = _make_samples()
        bad_baseline: dict[str, Any] = {
            "paths": {"button_to_generic_feedback": {"no_p95_here": True}}
        }
        # Must not raise KeyError
        regressions = check_regressions(fast, bad_baseline)
        assert isinstance(regressions, list)
        assert not any("button_to_generic_feedback" in r for r in regressions)

    def test_single_outlier_in_samples_triggers_regression(self) -> None:
        """One extreme value among otherwise fast samples pushes p95 up — must trip."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        target = "face_to_stable_identity"
        base_p95 = baseline["paths"][target]["p95_ms"]

        # nearest-rank p95 for n=50: index = ceil(0.95*50)-1 = 47.
        # Need slow_val at index 47 or higher.  Use 3 slow values (indices 47,48,49).
        regressing = dict(fast)
        fast_val = base_p95 * 0.1
        slow_val = base_p95 * (REGRESSION_FACTOR + 1.0)
        regressing[target] = [fast_val] * 47 + [slow_val] * 3  # indices 47-49 → slow
        regressions = check_regressions(regressing, baseline)
        assert any(target in r for r in regressions), (
            f"Expected regression for {target!r}. regressions={regressions}"
        )


# ---------------------------------------------------------------------------
# build_json
# ---------------------------------------------------------------------------


class TestBuildJson:
    def test_required_top_level_keys(self) -> None:
        report = build_json(_make_samples(), [])
        assert {"regressions", "regression_factor", "paths"} <= report.keys()

    def test_path_entry_has_percentiles(self) -> None:
        report = build_json(_make_samples(), [])
        entry = report["paths"]["button_to_generic_feedback"]
        assert {"p50_ms", "p95_ms", "p99_ms", "count", "budget_p95_ms"} <= entry.keys()

    def test_webrtc_marked_simulator_na(self) -> None:
        report = build_json(_make_samples(), [])
        assert report["paths"]["webrtc_glass_to_glass"] == {"simulator_na": True}

    def test_regressions_in_output(self) -> None:
        report = build_json(_make_samples(), ["button_to_generic_feedback: ..."])
        assert len(report["regressions"]) == 1

    def test_empty_samples_path_excluded(self) -> None:
        samples = _make_samples()
        samples["button_to_generic_feedback"] = []
        report = build_json(samples, [])
        assert "button_to_generic_feedback" not in report["paths"]


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


class TestBuildReport:
    def test_all_paths_present(self) -> None:
        text = build_report(_make_samples(), baseline=None, regressions=[])
        for path in BUDGET_P95_MS:
            assert path in text

    def test_regression_marker_present(self) -> None:
        text = build_report(
            _make_samples(),
            baseline=None,
            regressions=["button_to_generic_feedback: current p95=999ms"],
        )
        assert "REGRESSION" in text or "regression" in text.lower()

    def test_no_baseline_note_shown(self) -> None:
        text = build_report(_make_samples(), baseline=None, regressions=[])
        assert "No baseline" in text or "baseline" in text.lower()

    def test_no_regression_ok_note_when_baseline_present(self) -> None:
        samples = _make_samples()
        baseline = _make_baseline(samples)
        text = build_report(samples, baseline=baseline, regressions=[])
        assert "✓" in text or "No regression" in text.lower() or "no regressions" in text.lower()

    def test_webrtc_simulator_na_label(self) -> None:
        text = build_report(_make_samples(), baseline=None, regressions=[])
        assert "N/A" in text or "simulator N/A" in text


# ---------------------------------------------------------------------------
# load_baseline / save_baseline round-trip
# ---------------------------------------------------------------------------


class TestBaselineRoundTrip:
    def test_save_and_load(self) -> None:
        samples = _make_samples()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            save_baseline(path, samples, [])
            loaded = load_baseline(path)
            assert loaded is not None
            regressions = check_regressions(samples, loaded)
            assert regressions == []
        finally:
            path.unlink(missing_ok=True)

    def test_load_missing_returns_none(self) -> None:
        result = load_baseline(Path("/nonexistent/baseline.json"))
        assert result is None

    def test_baseline_json_is_valid(self) -> None:
        samples = _make_samples()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            save_baseline(path, samples, [])
            data = json.loads(path.read_text())
            assert isinstance(data["paths"], dict)
        finally:
            path.unlink(missing_ok=True)
