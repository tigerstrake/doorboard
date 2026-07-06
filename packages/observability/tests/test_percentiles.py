"""Unit tests for packages/observability/percentiles.py."""

from __future__ import annotations

import pytest
from doorboard_observability.percentiles import p50, p95, p99, percentile, summary


class TestPercentile:
    def test_single_value(self) -> None:
        assert percentile([42.0], 50) == 42.0

    def test_two_values_p50(self) -> None:
        # Nearest-rank: ceil(50/100 * 2) - 1 = ceil(1) - 1 = 0 → sorted[0]
        assert percentile([10.0, 20.0], 50) == 10.0

    def test_two_values_p95(self) -> None:
        # ceil(95/100 * 2) - 1 = ceil(1.9) - 1 = 2 - 1 = 1 → sorted[1]
        assert percentile([10.0, 20.0], 95) == 20.0

    def test_five_values_p50(self) -> None:
        # ceil(50/100 * 5) - 1 = ceil(2.5) - 1 = 3 - 1 = 2 → sorted[2]
        data = [5.0, 3.0, 1.0, 4.0, 2.0]
        assert percentile(data, 50) == 3.0

    def test_five_values_p95(self) -> None:
        # ceil(95/100 * 5) - 1 = ceil(4.75) - 1 = 5 - 1 = 4 → sorted[4]
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert percentile(data, 95) == 5.0

    def test_p0_returns_min(self) -> None:
        # ceil(0 * n) - 1 = max(0, -1) = 0 → sorted[0]
        assert percentile([7.0, 1.0, 5.0], 0) == 1.0

    def test_p100_returns_max(self) -> None:
        # ceil(100/100 * 3) - 1 = 3 - 1 = 2 → sorted[2]
        assert percentile([7.0, 1.0, 5.0], 100) == 7.0

    def test_sorted_input_unchanged(self) -> None:
        data = [1.0, 2.0, 3.0]
        _ = percentile(data, 50)
        assert data == [1.0, 2.0, 3.0]

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            percentile([], 50)

    def test_p_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="\\[0, 100\\]"):
            percentile([1.0], 101)
        with pytest.raises(ValueError, match="\\[0, 100\\]"):
            percentile([1.0], -1)

    def test_large_dataset_monotonic(self) -> None:
        """p50 ≤ p95 ≤ p99 for any non-trivial dataset."""
        data = [float(x) for x in range(1, 1001)]
        assert p50(data) <= p95(data) <= p99(data)

    def test_100_samples_p95_is_95th(self) -> None:
        """With 100 values [1..100], p95 must be 95."""
        data = [float(x) for x in range(1, 101)]
        assert p95(data) == 95.0

    def test_20_samples_p95_is_19th(self) -> None:
        """With 20 values [1..20], nearest-rank p95: ceil(0.95*20)-1 = 18."""
        data = [float(x) for x in range(1, 21)]
        assert p95(data) == 19.0


class TestSummary:
    def test_summary_keys(self) -> None:
        result = summary([1.0, 2.0, 3.0])
        assert set(result.keys()) == {"p50_ms", "p95_ms", "p99_ms", "min_ms", "max_ms", "count"}

    def test_summary_count(self) -> None:
        result = summary([1.0, 2.0, 3.0])
        assert result["count"] == 3.0

    def test_summary_min_max(self) -> None:
        result = summary([5.0, 1.0, 3.0])
        assert result["min_ms"] == 1.0
        assert result["max_ms"] == 5.0

    def test_summary_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            summary([])

    def test_summary_single_value_all_equal(self) -> None:
        result = summary([42.0])
        assert result["p50_ms"] == result["p95_ms"] == result["p99_ms"] == 42.0
