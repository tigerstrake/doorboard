"""Unit tests for packages/observability/clock_offset.py."""

from __future__ import annotations

import pytest
from doorboard_observability.clock_offset import ClockOffsetEstimator, OffsetSample


class TestOffsetSample:
    def test_rtt(self) -> None:
        s = OffsetSample(t_pi_send_ms=1000.0, t_esp_recv_ms=1003.0, t_pi_ack_ms=1006.0)
        assert s.rtt_ms == pytest.approx(6.0)

    def test_one_way(self) -> None:
        s = OffsetSample(t_pi_send_ms=1000.0, t_esp_recv_ms=1003.0, t_pi_ack_ms=1006.0)
        assert s.one_way_ms == pytest.approx(3.0)

    def test_offset_zero_when_clocks_agree(self) -> None:
        # If ESP clock == Pi clock (same domain), offset should be 0
        s = OffsetSample(t_pi_send_ms=1000.0, t_esp_recv_ms=1003.0, t_pi_ack_ms=1006.0)
        # offset = t_esp_recv - (t_pi_send + one_way) = 1003 - (1000 + 3) = 0
        assert s.offset_ms == pytest.approx(0.0)

    def test_offset_positive_when_esp_ahead(self) -> None:
        # ESP32 started 500 ms before Pi (its monotonic is 500 ms ahead)
        s = OffsetSample(t_pi_send_ms=1000.0, t_esp_recv_ms=1503.0, t_pi_ack_ms=1006.0)
        # offset = 1503 - (1000 + 3) = 500
        assert s.offset_ms == pytest.approx(500.0)

    def test_max_error_equals_half_rtt(self) -> None:
        s = OffsetSample(t_pi_send_ms=1000.0, t_esp_recv_ms=1003.0, t_pi_ack_ms=1006.0)
        assert s.max_error_ms == pytest.approx(3.0)


class TestClockOffsetEstimator:
    def _add(
        self,
        est: ClockOffsetEstimator,
        *,
        pi_send: float,
        esp_recv: float,
        pi_ack: float,
    ) -> OffsetSample:
        return est.add_sample(
            t_pi_send_ms=pi_send,
            t_esp_recv_ms=esp_recv,
            t_pi_ack_ms=pi_ack,
        )

    def test_no_samples_returns_zero_offset(self) -> None:
        est = ClockOffsetEstimator()
        assert est.estimated_offset_ms == 0.0

    def test_no_samples_max_error_is_inf(self) -> None:
        est = ClockOffsetEstimator()
        assert est.max_error_ms == float("inf")

    def test_single_sample_offset(self) -> None:
        est = ClockOffsetEstimator()
        self._add(est, pi_send=0.0, esp_recv=500.0, pi_ack=4.0)
        # offset = 500 - (0 + 2) = 498
        assert est.estimated_offset_ms == pytest.approx(498.0)

    def test_to_pi_ms_converts_correctly(self) -> None:
        est = ClockOffsetEstimator()
        self._add(est, pi_send=0.0, esp_recv=500.0, pi_ack=4.0)
        # esp_ts 600 → pi_equivalent = 600 - 498 = 102
        assert est.to_pi_ms(600.0) == pytest.approx(102.0)

    def test_no_samples_to_pi_ms_passthrough(self) -> None:
        est = ClockOffsetEstimator()
        assert est.to_pi_ms(1234.5) == pytest.approx(1234.5)

    def test_median_rejects_outlier(self) -> None:
        est = ClockOffsetEstimator()
        # 4 samples with offset ~0, one wild outlier
        for _ in range(4):
            self._add(est, pi_send=1000.0, esp_recv=1003.0, pi_ack=1006.0)
        # outlier: ESP clock is 10 s ahead
        self._add(est, pi_send=1000.0, esp_recv=11003.0, pi_ack=1006.0)
        # Median of [0, 0, 0, 0, 10000] = 0 (index 2 of 5)
        assert est.estimated_offset_ms == pytest.approx(0.0)

    def test_window_limits_samples(self) -> None:
        est = ClockOffsetEstimator(max_samples=3)
        for i in range(10):
            self._add(
                est, pi_send=float(i * 100), esp_recv=float(i * 100 + 50), pi_ack=float(i * 100 + 6)
            )
        assert est.sample_count() == 3

    def test_ack_before_send_raises(self) -> None:
        est = ClockOffsetEstimator()
        with pytest.raises(ValueError, match="t_pi_ack_ms"):
            est.add_sample(t_pi_send_ms=1000.0, t_esp_recv_ms=1003.0, t_pi_ack_ms=999.0)

    def test_clear_resets_samples(self) -> None:
        est = ClockOffsetEstimator()
        self._add(est, pi_send=0.0, esp_recv=500.0, pi_ack=4.0)
        est.clear()
        assert not est.has_samples
        assert est.estimated_offset_ms == 0.0

    def test_max_error_is_median_half_rtt(self) -> None:
        est = ClockOffsetEstimator()
        # 3 samples: RTTs 6, 10, 4 → sorted [4, 6, 10] → median 6 → half=3
        self._add(est, pi_send=0.0, esp_recv=3.0, pi_ack=6.0)  # rtt=6
        self._add(est, pi_send=0.0, esp_recv=5.0, pi_ack=10.0)  # rtt=10
        self._add(est, pi_send=0.0, esp_recv=2.0, pi_ack=4.0)  # rtt=4
        assert est.max_error_ms == pytest.approx(3.0)
