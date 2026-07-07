"""Clock abstraction for door-visiond.

All durations and expiries use a *monotonic* clock (never wall time), per
ARCHITECTURE.md §7.  Wall time is UTC and used only for event ``occurred_at``
and ``expires_at`` fields.  Tests inject :class:`FakeClock` so stability,
cooldown, and TTL logic is deterministic without ``sleep``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def monotonic_ms(self) -> int:
        """Monotonic milliseconds — for durations, TTLs, and expiries."""
        ...

    def utc_now(self) -> datetime:
        """Timezone-aware UTC wall-clock time — for event timestamps only."""
        ...


class SystemClock:
    """Production clock backed by ``time.monotonic_ns`` and ``datetime.now``."""

    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000

    def utc_now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Deterministic clock for tests; time only advances when told to."""

    def __init__(self, *, start_ms: int = 0, start_utc: datetime | None = None) -> None:
        self._mono_ms = start_ms
        self._utc = start_utc or datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)

    def monotonic_ms(self) -> int:
        return self._mono_ms

    def utc_now(self) -> datetime:
        return self._utc

    def advance(self, ms: int) -> None:
        self._mono_ms += ms
        self._utc = self._utc + timedelta(milliseconds=ms)
