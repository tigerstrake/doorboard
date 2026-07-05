from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass
class SimClock:
    base_utc: datetime = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
    monotonic_ms: int = 0

    def utc_now(self) -> datetime:
        return self.base_utc + timedelta(milliseconds=self.monotonic_ms)

    def advance_to(self, monotonic_ms: int) -> None:
        if monotonic_ms < self.monotonic_ms:
            msg = "scenario time cannot move backwards"
            raise ValueError(msg)
        self.monotonic_ms = monotonic_ms

    def advance_by(self, delta_ms: int) -> None:
        if delta_ms < 0:
            msg = "delta_ms must be non-negative"
            raise ValueError(msg)
        self.monotonic_ms += delta_ms
