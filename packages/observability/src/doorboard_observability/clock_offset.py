"""Clock-offset estimation for cross-device timing (ESP32 ↔ Pi).

Problem
-------
The ESP32 runs its own monotonic clock (esp_timer_get_time) that starts at
boot and has no relationship to the Pi's CLOCK_MONOTONIC.  We must never
assume the two clocks are synchronized.

Offset estimation method
------------------------
We use an ack round-trip halving approach (analogous to NTP's algorithm):

    1. Pi records its own monotonic time t_pi_send.
    2. Pi sends a PING message to the ESP32.
    3. ESP32 immediately records its monotonic time t_esp_recv and replies
       with an ACK that includes t_esp_recv.
    4. Pi records t_pi_ack on receipt of the ACK.

    Assuming symmetric one-way delay d = (t_pi_ack - t_pi_send) / 2:

        offset_estimate = t_esp_recv - (t_pi_send + d)
                        = t_esp_recv - t_pi_send - d

    So:  t_pi_equivalent(t_esp) = t_esp - offset_estimate

Error bound
-----------
The half-trip assumption introduces an error of at most d/2, where d is the
round-trip latency.  At 115 200 baud UART with a 64-byte ping frame:

    d_serial ≈ 64 * 10 / 115200 ≈ 5.6 ms (per direction)

    Maximum absolute error ≈ d_total / 2

In practice with the simulator both halves are zero-latency so error = 0.
On real hardware with a short UART cable, d_total is typically 2–6 ms,
giving a worst-case error of 1–3 ms.  The button→feedback target (30 ms
p95) has 3–15% timing error, which is acceptable for a measurement harness.

The error bound must be measured and recorded on bench; see
tests/hardware-in-loop/T-104-latency-harness.md.

Usage
-----
    from doorboard_observability.clock_offset import ClockOffsetEstimator

    est = ClockOffsetEstimator()
    # After a PING/ACK exchange, add a sample:
    est.add_sample(t_pi_send_ms=1000, t_esp_recv_ms=2030, t_pi_ack_ms=1006)
    # Convert an ESP32 monotonic timestamp to Pi time:
    pi_equivalent = est.to_pi_ms(t_esp_ms)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class OffsetSample:
    """One offset measurement from a single PING/ACK round-trip."""

    t_pi_send_ms: float
    t_esp_recv_ms: float
    t_pi_ack_ms: float

    @property
    def rtt_ms(self) -> float:
        """One-way latency estimate (half the round-trip time)."""
        return self.t_pi_ack_ms - self.t_pi_send_ms

    @property
    def one_way_ms(self) -> float:
        return self.rtt_ms / 2.0

    @property
    def offset_ms(self) -> float:
        """Estimated offset: esp_clock - pi_clock at the moment of receipt.

        Positive means the ESP32 clock is ahead of the Pi clock (as it would
        be if the ESP32 booted earlier).
        """
        return self.t_esp_recv_ms - (self.t_pi_send_ms + self.one_way_ms)

    @property
    def max_error_ms(self) -> float:
        """Worst-case absolute error due to asymmetry assumption."""
        return self.rtt_ms / 2.0


@dataclass
class ClockOffsetEstimator:
    """Maintains a sliding window of PING/ACK samples and provides offset estimates.

    Only a limited window is kept to handle ESP32 reboots (counter reset) or
    drift over long uptime — a stale offset is worse than a fresh one.

    Args:
        max_samples: Number of recent samples to retain (default: 20).
    """

    max_samples: int = 20
    _samples: deque[OffsetSample] = field(default_factory=deque, repr=False)

    def __post_init__(self) -> None:
        self._samples = deque(maxlen=self.max_samples)

    def add_sample(
        self,
        *,
        t_pi_send_ms: float,
        t_esp_recv_ms: float,
        t_pi_ack_ms: float,
    ) -> OffsetSample:
        """Record a new PING/ACK round-trip and return the computed sample."""
        if t_pi_ack_ms < t_pi_send_ms:
            msg = "t_pi_ack_ms must be >= t_pi_send_ms"
            raise ValueError(msg)
        sample = OffsetSample(
            t_pi_send_ms=t_pi_send_ms,
            t_esp_recv_ms=t_esp_recv_ms,
            t_pi_ack_ms=t_pi_ack_ms,
        )
        self._samples.append(sample)
        return sample

    @property
    def has_samples(self) -> bool:
        return len(self._samples) > 0

    @property
    def estimated_offset_ms(self) -> float:
        """Median offset across all retained samples (milliseconds).

        Returns 0.0 if no samples have been collected (safe fallback: no
        adjustment applied, measurement error = full ESP32 uptime).

        Uses the median rather than mean to reject outliers caused by UART
        framing glitches or OS scheduler jitter during the round-trip.
        """
        if not self._samples:
            return 0.0
        offsets = sorted(s.offset_ms for s in self._samples)
        mid = len(offsets) // 2
        if len(offsets) % 2 == 0:
            return (offsets[mid - 1] + offsets[mid]) / 2.0
        return offsets[mid]

    @property
    def max_error_ms(self) -> float:
        """Conservative upper bound on error: median half-RTT of retained samples."""
        if not self._samples:
            return float("inf")
        rtts = sorted(s.rtt_ms for s in self._samples)
        mid = len(rtts) // 2
        median_rtt = (rtts[mid - 1] + rtts[mid]) / 2.0 if len(rtts) % 2 == 0 else rtts[mid]
        return median_rtt / 2.0

    def to_pi_ms(self, esp_monotonic_ms: float) -> float:
        """Convert an ESP32 monotonic timestamp to the equivalent Pi monotonic time.

        If no samples are available, returns *esp_monotonic_ms* unchanged and
        the caller should treat the measurement as approximate.
        """
        return esp_monotonic_ms - self.estimated_offset_ms

    def sample_count(self) -> int:
        return len(self._samples)

    def clear(self) -> None:
        """Discard all retained samples (e.g., after ESP32 reboot)."""
        self._samples.clear()
