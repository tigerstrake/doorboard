"""Tests for the latency harness.

The gate logic is imported from doorboard_observability.harness_core (installed
as an editable package); the two in-process measurements from
tests.performance.harness. No sys.path manipulation is needed for either.

Test coverage:
- check_regressions: intentionally slowed fixture MUST trip the check
  (acceptance criterion from the brief).
- No regression below the 3× threshold; only the regressing path is flagged.
- N/A paths — by name (SIMULATOR_NA) or the legacy all-zero sentinel — are
  never flagged and are labelled, with a reason, in both report formats.
- Unknown paths in samples, empty samples, malformed baseline — all safe.
- build_json / build_report produce well-formed output.
- Baseline round-trips correctly via load_baseline / save_baseline.
- The two measured paths really run through door-api and door-media.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from doorboard_observability.harness_core import (
    BUDGET_P95_MS,
    MEASURED_PATHS,
    REGRESSION_FACTOR,
    SIMULATOR_NA,
    build_json,
    build_report,
    check_regressions,
    load_baseline,
    save_baseline,
)
from tests.performance.harness import (
    measure_bell_to_recording_event,
    measure_bell_to_visitor_mode,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_samples(*, multiplier: float = 1.0) -> dict[str, list[float]]:
    """Synthetic samples: every measured path at 10% of budget × multiplier, N/A empty."""
    result: dict[str, list[float]] = {}
    for path, budget in BUDGET_P95_MS.items():
        if path in SIMULATOR_NA:
            result[path] = []
            continue
        val = budget * 0.10 * multiplier
        result[path] = [val] * 50
    return result


def _make_baseline(samples: dict[str, list[float]]) -> dict[str, Any]:
    return build_json(samples, [])


# ---------------------------------------------------------------------------
# The path split itself
# ---------------------------------------------------------------------------


class TestPathSplit:
    def test_every_budgeted_path_is_measured_or_na_never_both(self) -> None:
        assert set(MEASURED_PATHS) | set(SIMULATOR_NA) == set(BUDGET_P95_MS)
        assert not set(MEASURED_PATHS) & set(SIMULATOR_NA)

    def test_the_two_bell_paths_are_the_measured_ones(self) -> None:
        assert set(MEASURED_PATHS) == {"bell_to_visitor_mode", "bell_to_recording_event"}

    def test_every_na_path_says_why(self) -> None:
        for path, reason in SIMULATOR_NA.items():
            assert reason.strip(), f"{path!r} needs a reason"


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
        for path in MEASURED_PATHS:
            assert any(path in r for r in regressions), (
                f"Expected regression for {path!r} not found in {regressions}"
            )

    def test_exactly_at_threshold_not_regression(self) -> None:
        """Exactly REGRESSION_FACTOR× is at the limit but must NOT be flagged
        (boundary: > not >=)."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        at_threshold: dict[str, list[float]] = {}
        for path in fast:
            if path in SIMULATOR_NA:
                at_threshold[path] = []
            else:
                base_p95 = baseline["paths"][path]["p95_ms"]
                at_threshold[path] = [base_p95 * REGRESSION_FACTOR] * 50
        regressions = check_regressions(at_threshold, baseline)
        assert regressions == [], f"Should not flag at exactly threshold: {regressions}"

    def test_only_slow_path_flagged(self) -> None:
        """Only the one regressing path should appear in the regression list."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        target = "bell_to_visitor_mode"
        base_p95 = baseline["paths"][target]["p95_ms"]

        regressing = dict(fast)
        regressing[target] = [base_p95 * (REGRESSION_FACTOR + 1.0)] * 50

        regressions = check_regressions(regressing, baseline)
        assert any(target in r for r in regressions), "Target must be flagged"
        for path in MEASURED_PATHS:
            if path != target:
                assert not any(path in r for r in regressions), (
                    f"{path!r} should NOT be a regression"
                )

    def test_na_paths_never_regress_even_with_samples(self) -> None:
        """A path that is N/A by name is excluded whatever its samples say."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        current = dict(fast)
        for path in SIMULATOR_NA:
            current[path] = [999_999.0] * 50
        assert check_regressions(current, baseline) == []

    def test_legacy_zero_sentinel_still_na(self) -> None:
        """An all-zero sample list (the old N/A marker) stays excluded and labelled."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        current = dict(fast)
        current["future_bench_only_path"] = [0.0] * 50
        assert check_regressions(current, baseline) == []
        assert build_json(current, [])["paths"]["future_bench_only_path"]["simulator_na"] is True

    def test_empty_samples_not_regression(self) -> None:
        """Empty sample list for a measured path must not be flagged and must not raise."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        current = dict(fast)
        current["bell_to_visitor_mode"] = []
        regressions = check_regressions(current, baseline)
        assert not any("bell_to_visitor_mode" in r for r in regressions)

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
        bad_baseline: dict[str, Any] = {"paths": {"bell_to_visitor_mode": {"no_p95_here": True}}}
        # Must not raise KeyError
        regressions = check_regressions(fast, bad_baseline)
        assert isinstance(regressions, list)
        assert not any("bell_to_visitor_mode" in r for r in regressions)

    def test_single_outlier_in_samples_triggers_regression(self) -> None:
        """One extreme value among otherwise fast samples pushes p95 up — must trip."""
        fast = _make_samples()
        baseline = _make_baseline(fast)
        target = "bell_to_recording_event"
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
        entry = report["paths"]["bell_to_visitor_mode"]
        assert {"p50_ms", "p95_ms", "p99_ms", "count", "budget_p95_ms"} <= entry.keys()

    def test_na_paths_marked_with_reason_even_without_samples(self) -> None:
        report = build_json({"bell_to_visitor_mode": [1.0]}, [])
        for path, reason in SIMULATOR_NA.items():
            assert report["paths"][path] == {"simulator_na": True, "reason": reason}

    def test_regressions_in_output(self) -> None:
        report = build_json(_make_samples(), ["bell_to_visitor_mode: ..."])
        assert len(report["regressions"]) == 1

    def test_unrun_measured_path_excluded(self) -> None:
        samples = _make_samples()
        samples["bell_to_visitor_mode"] = []
        report = build_json(samples, [])
        assert "bell_to_visitor_mode" not in report["paths"]
        assert "bell_to_recording_event" in report["paths"]


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


class TestBuildReport:
    def test_all_paths_present(self) -> None:
        text = build_report(_make_samples(), baseline=None, regressions=[])
        for path in BUDGET_P95_MS:
            assert path in text

    def test_measured_and_na_paths_are_told_apart(self) -> None:
        text = build_report(_make_samples(), baseline=None, regressions=[])
        for line in text.splitlines():
            for path in MEASURED_PATHS:
                if line.strip().startswith(path):
                    assert "measured" in line, line
            for path in SIMULATOR_NA:
                if line.strip().startswith(path):
                    assert "simulator N/A" in line, line
        for path, reason in SIMULATOR_NA.items():
            assert f"{path}: {reason}" in text

    def test_regression_marker_present(self) -> None:
        text = build_report(
            _make_samples(),
            baseline=None,
            regressions=["bell_to_visitor_mode: current p95=999ms"],
        )
        assert "REGRESSION" in text

    def test_no_baseline_note_shown(self) -> None:
        text = build_report(_make_samples(), baseline=None, regressions=[])
        assert "No baseline" in text

    def test_no_regression_ok_note_when_baseline_present(self) -> None:
        samples = _make_samples()
        baseline = _make_baseline(samples)
        text = build_report(samples, baseline=baseline, regressions=[])
        assert "No regressions vs baseline" in text


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


# ---------------------------------------------------------------------------
# The measurements themselves run real service code
# ---------------------------------------------------------------------------


class TestInProcessMeasurements:
    def test_bell_to_visitor_mode_runs_door_api_end_to_end(self, tmp_path: Path) -> None:
        samples = measure_bell_to_visitor_mode(5, tmp_path / "door-api")

        assert len(samples) == 5
        assert all(0.0 < s < BUDGET_P95_MS["bell_to_visitor_mode"] for s in samples), samples
        # The session store is real and on disk, and every press was a full session:
        # BUTTON_PRESSED → VISITOR_MODE, ended by the admin route, back to IDLE.
        assert (tmp_path / "door-api" / "session.sqlite").exists()
        from door_api.app import state

        metrics = state.machine.metrics.to_dict()
        assert metrics["session_sessions_started_total"] == 5
        assert metrics["session_sessions_ended_total"] == 5
        assert metrics["session_illegal_transitions_total"] == 0

    def test_bell_to_recording_event_runs_door_media_end_to_end(self, tmp_path: Path) -> None:
        samples = asyncio.run(measure_bell_to_recording_event(5, tmp_path / "door-media"))

        assert len(samples) == 5
        assert all(0.0 < s < BUDGET_P95_MS["bell_to_recording_event"] for s in samples), samples
        # The recording registry is real and on disk: five distinct session events
        # were accepted (a replay would be refused as a duplicate), and every
        # window was discarded after its sample so no recording row is left.
        with sqlite3.connect(tmp_path / "door-media" / "door_media.db") as conn:
            processed = conn.execute("SELECT COUNT(*) FROM processed_session_events").fetchone()
            recordings = conn.execute("SELECT COUNT(*) FROM recordings").fetchone()
        assert processed[0] == 5
        assert recordings[0] == 0
