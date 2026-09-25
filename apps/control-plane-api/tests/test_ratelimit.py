"""The failure-keyed limiter behind the 429s on the key-release route and /admin/*."""

from __future__ import annotations

import pytest
from control_plane_api.ratelimit import FailureRateLimiter


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_blocks_after_the_limit_and_lifts_when_the_window_passes() -> None:
    clock = _Clock()
    limiter = FailureRateLimiter(limit=5, window_s=60, clock=clock)
    for i in range(5):
        assert limiter.is_blocked("10.0.0.7") is False
        assert limiter.record_failure("10.0.0.7") == i + 1
    assert limiter.is_blocked("10.0.0.7") is True
    assert limiter.is_blocked("10.0.0.8") is False  # per address

    clock.now += 59
    assert limiter.is_blocked("10.0.0.7") is True
    clock.now += 2  # the oldest failure is now outside the window
    assert limiter.is_blocked("10.0.0.7") is False
    assert limiter.record_failure("10.0.0.7") == 1


def test_only_the_failures_inside_the_window_count() -> None:
    clock = _Clock()
    limiter = FailureRateLimiter(limit=3, window_s=60, clock=clock)
    limiter.record_failure("a")
    clock.now += 30
    limiter.record_failure("a")
    clock.now += 31  # first failure ages out
    assert limiter.record_failure("a") == 2
    assert limiter.is_blocked("a") is False


def test_tracked_addresses_are_bounded() -> None:
    clock = _Clock()
    limiter = FailureRateLimiter(limit=5, window_s=60, max_tracked=3, clock=clock)
    for i, ip in enumerate(("a", "b", "c", "d")):
        clock.now += 1
        for _ in range(5):
            limiter.record_failure(ip)
        assert limiter.is_blocked(ip) is True, i
    # "a" (the oldest) was evicted to keep memory bounded; the newest are kept.
    assert limiter.is_blocked("a") is False
    assert limiter.is_blocked("d") is True


def test_a_zero_limit_is_refused() -> None:
    with pytest.raises(ValueError):
        FailureRateLimiter(limit=0)
