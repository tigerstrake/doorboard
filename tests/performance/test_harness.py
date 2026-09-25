"""Tests for the latency harness.

The gate logic is imported from doorboard_observability.harness_core (installed
as an editable package); the two in-process measurements from
tests.performance.harness. No sys.path manipulation is needed for either.

Test coverage:
- check_regressions: intentionally slowed fixture MUST trip the check
  (acceptance criterion from the brief).
- No regression below the 3× threshold; only the regressing path is flagged;
  the threshold never drops below 2% of the path's budget.
- check_budgets: a measured path over its §4 budget fails with no baseline at all.
- median_baseline folds several runs into a per-stat median.
- N/A paths — by name (SIMULATOR_NA) or the legacy all-zero sentinel — are
  never flagged and are labelled, with a reason, in both report formats.
- Unknown paths in samples, empty samples, malformed baseline — all safe.
- build_json / build_report produce well-formed output.
- Baseline round-trips correctly via load_baseline / save_baseline.
- The two measured paths really run through door-api and door-media.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import pytest
from doorboard_observability.harness_core import (
    BUDGET_P95_MS,
    MEASURED_PATHS,
    REGRESSION_FACTOR,
    REGRESSION_FLOOR_FRACTION,
    SIMULATOR_NA,
    build_json,
    build_report,
    check_budgets,
    check_regressions,
    load_baseline,
    median_baseline,
    save_baseline,
    write_baseline,
)
from tests.performance import harness
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

    def test_floor_ignores_jitter_on_sub_millisecond_baselines(self) -> None:
        """A 0.5 ms baseline on a 250 ms budget has a 5 ms floor: 4 ms (8×) is noise."""
        target = "bell_to_visitor_mode"
        fast = _make_samples()
        fast[target] = [0.5] * 50
        baseline = _make_baseline(fast)
        current = dict(fast)
        current[target] = [4.0] * 50
        assert check_regressions(current, baseline) == []

    def test_floor_is_two_percent_of_budget(self) -> None:
        """...and 5.5 ms is past the floor — the smallest change the budget cares about."""
        target = "bell_to_visitor_mode"
        floor = REGRESSION_FLOOR_FRACTION * BUDGET_P95_MS[target]
        assert floor == 5.0
        fast = _make_samples()
        fast[target] = [0.5] * 50
        baseline = _make_baseline(fast)
        current = dict(fast)
        current[target] = [floor + 0.5] * 50
        regressions = check_regressions(current, baseline)
        assert len(regressions) == 1 and regressions[0].startswith(f"{target}:")
        assert f"threshold={floor:.1f}ms" in regressions[0]

    def test_factor_rules_once_the_baseline_is_above_the_floor(self) -> None:
        """A 10 ms baseline (3× = 30 ms > the 5 ms floor) regresses at 30 ms, not 5."""
        target = "bell_to_visitor_mode"
        fast = _make_samples()
        fast[target] = [10.0] * 50
        baseline = _make_baseline(fast)
        just_under = dict(fast)
        just_under[target] = [29.0] * 50
        assert check_regressions(just_under, baseline) == []
        just_over = dict(fast)
        just_over[target] = [31.0] * 50
        assert [r.partition(":")[0] for r in check_regressions(just_over, baseline)] == [target]


# ---------------------------------------------------------------------------
# check_budgets — the gate that needs no baseline
# ---------------------------------------------------------------------------


class TestBudgetGate:
    def test_under_budget_passes(self) -> None:
        assert check_budgets(_make_samples()) == []

    def test_over_budget_measured_path_fails_without_a_baseline(self) -> None:
        target = "bell_to_recording_event"
        samples = _make_samples()
        samples[target] = [BUDGET_P95_MS[target] + 1.0] * 50
        failures = check_budgets(samples)
        assert len(failures) == 1
        assert failures[0].startswith(f"{target}:")
        assert "budget" in failures[0]

    def test_exactly_at_budget_passes(self) -> None:
        target = "bell_to_recording_event"
        samples = _make_samples()
        samples[target] = [BUDGET_P95_MS[target]] * 50
        assert check_budgets(samples) == []

    def test_na_paths_never_fail_the_budget(self) -> None:
        samples = _make_samples()
        for path in SIMULATOR_NA:
            samples[path] = [999_999.0] * 50
        assert check_budgets(samples) == []

    def test_a_baseline_saved_over_budget_does_not_launder_it(self) -> None:
        """Regression-free vs a bad baseline, still a budget failure."""
        target = "bell_to_visitor_mode"
        slow = _make_samples()
        slow[target] = [BUDGET_P95_MS[target] * 2] * 50
        baseline = _make_baseline(slow)
        assert check_regressions(slow, baseline) == []
        assert [f.partition(":")[0] for f in check_budgets(slow)] == [target]

    def test_report_marks_the_row_and_summarises(self) -> None:
        target = "bell_to_visitor_mode"
        samples = _make_samples()
        samples[target] = [BUDGET_P95_MS[target] + 1.0] * 50
        failures = check_budgets(samples)
        text = build_report(samples, baseline=None, regressions=[], budget_failures=failures)
        row = next(line for line in text.splitlines() if line.strip().startswith(target))
        assert "OVER BUDGET" in row
        assert "over the §4 budget" in text

    def test_json_carries_the_flag_and_the_failures(self) -> None:
        target = "bell_to_visitor_mode"
        samples = _make_samples()
        samples[target] = [BUDGET_P95_MS[target] + 1.0] * 50
        failures = check_budgets(samples)
        report = build_json(samples, [], budget_failures=failures)
        assert report["paths"][target]["over_budget"] is True
        assert report["paths"]["bell_to_recording_event"]["over_budget"] is False
        assert report["budget_failures"] == failures


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

    def test_regression_marker_on_the_row_and_in_the_summary(self) -> None:
        text = build_report(
            _make_samples(),
            baseline=None,
            regressions=["bell_to_visitor_mode: current p95=999ms"],
        )
        row = next(line for line in text.splitlines() if "bell_to_visitor_mode" in line)
        assert "REGRESSION" in row
        assert "regression(s) detected" in text
        assert f"{REGRESSION_FLOOR_FRACTION:.0%} of budget" in text

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
# median_baseline — several runs folded into one
# ---------------------------------------------------------------------------


class TestMedianBaseline:
    def _reports(self) -> list[dict[str, Any]]:
        reports = []
        for p95_ms in (1.0, 10.0, 2.0):
            samples = _make_samples()
            samples["bell_to_visitor_mode"] = [p95_ms] * 50
            reports.append(build_json(samples, []))
        return reports

    def test_takes_the_per_stat_median(self) -> None:
        folded = median_baseline(self._reports())
        entry = folded["paths"]["bell_to_visitor_mode"]
        assert entry["p95_ms"] == 2.0
        assert entry["count"] == 50
        assert entry["runs"] == 3
        assert entry["budget_p95_ms"] == BUDGET_P95_MS["bell_to_visitor_mode"]

    def test_na_entries_pass_through(self) -> None:
        folded = median_baseline(self._reports())
        for path, reason in SIMULATOR_NA.items():
            assert folded["paths"][path] == {"simulator_na": True, "reason": reason}

    def test_a_path_missing_from_one_run_is_dropped(self) -> None:
        reports = self._reports()
        del reports[1]["paths"]["bell_to_recording_event"]
        folded = median_baseline(reports)
        assert "bell_to_recording_event" not in folded["paths"]
        assert "bell_to_visitor_mode" in folded["paths"]

    def test_round_trips_as_a_baseline(self, tmp_path: Path) -> None:
        path = tmp_path / "baseline.json"
        write_baseline(path, median_baseline(self._reports()))
        loaded = load_baseline(path)
        assert loaded is not None
        assert loaded["regressions"] == [] and loaded["budget_failures"] == []
        samples = _make_samples()
        samples["bell_to_visitor_mode"] = [2.0] * 50
        assert check_regressions(samples, loaded) == []
        samples["bell_to_visitor_mode"] = [7.0] * 50  # > max(3 × 2.0, 5.0)
        assert len(check_regressions(samples, loaded)) == 1

    def test_needs_at_least_one_report(self) -> None:
        with pytest.raises(ValueError):
            median_baseline([])


# ---------------------------------------------------------------------------
# The CLI's verdict
# ---------------------------------------------------------------------------


def _args(**overrides: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "mode": "simulator",
        "save_baseline": False,
        "runs": 1,
        "json_out": None,
        "report_out": None,
    }
    return argparse.Namespace(**{**defaults, **overrides})


class TestCliVerdict:
    """_main with the collection stubbed: the exit code is the gate."""

    def _stub(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runs: list[dict]) -> None:
        queue = list(runs)
        monkeypatch.setattr(harness, "collect_samples", lambda: queue.pop(0))
        monkeypatch.setattr(harness, "BASELINE_PATH", tmp_path / "baseline.json")

    def test_clean_run_against_its_own_baseline_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._stub(monkeypatch, tmp_path, [_make_samples()])
        write_baseline(tmp_path / "baseline.json", build_json(_make_samples(), []))
        assert harness._main(_args()) == 0

    def test_regression_exits_one(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        slow = _make_samples(multiplier=REGRESSION_FACTOR + 1.0)
        self._stub(monkeypatch, tmp_path, [slow])
        write_baseline(tmp_path / "baseline.json", build_json(_make_samples(), []))
        assert harness._main(_args()) == 1

    def test_over_budget_exits_one_even_with_no_baseline(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        over = _make_samples()
        over["bell_to_visitor_mode"] = [BUDGET_P95_MS["bell_to_visitor_mode"] + 1.0] * 50
        self._stub(monkeypatch, tmp_path, [over])
        assert harness._main(_args()) == 1

    def test_report_out_labels_measured_and_na(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._stub(monkeypatch, tmp_path, [_make_samples()])
        out = tmp_path / "latency-report.txt"
        assert harness._main(_args(report_out=str(out))) == 0
        text = out.read_text()
        assert "IN-PROCESS" in text and "not hardware acceptance" in text
        assert text.count("✓ measured") == len(MEASURED_PATHS)
        assert text.count("simulator N/A") >= len(SIMULATOR_NA)

    def test_save_baseline_with_runs_writes_the_median(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runs = []
        for p95_ms in (1.0, 10.0, 2.0):
            samples = _make_samples()
            samples["bell_to_visitor_mode"] = [p95_ms] * 50
            runs.append(samples)
        self._stub(monkeypatch, tmp_path, runs)
        assert harness._main(_args(save_baseline=True, runs=3)) == 0
        saved = load_baseline(tmp_path / "baseline.json")
        assert saved is not None
        assert saved["paths"]["bell_to_visitor_mode"]["p95_ms"] == 2.0
        assert saved["paths"]["bell_to_visitor_mode"]["runs"] == 3


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
