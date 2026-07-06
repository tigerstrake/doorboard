"""In-memory sliding-window rate limiter for public social writes.

Enforced on two independent axes (per-IP and per-session-token) so that
rotating one dimension alone does not bypass the limit — both api-conventions
and the T-403 brief call for "per-IP+session rate limits".

Bounded memory: each key's deque is trimmed to the window on every check, and
``sweep`` drops keys with no recent activity, so long uptime does not grow the
map unbounded even under sustained abusive traffic from many distinct keys.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    """Sliding-window counter: at most ``limit`` events per ``window_s`` per key."""

    limit: int
    window_s: float
    now_fn: Callable[[], float] = field(default_factory=lambda: __import__("time").monotonic)
    _hits: dict[str, deque[float]] = field(default_factory=dict, init=False)

    def allow(self, key: str) -> bool:
        """Record an attempt for ``key`` and return whether it is within limits.

        Always records the attempt timestamp (even rejected ones) so that a
        client hammering the endpoint doesn't get a free pass once it drops
        back under the limit within the same window.
        """
        now = self.now_fn()
        # maxlen bounds worst-case memory per key even under a request flood
        # far exceeding `limit` within a single window.
        bucket = self._hits.setdefault(key, deque(maxlen=max(self.limit * 4, 16)))

        cutoff = now - self.window_s
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

        if len(bucket) >= self.limit:
            bucket.append(now)
            return False

        bucket.append(now)
        return True

    def sweep(self) -> int:
        """Drop keys with no hits inside the current window. Returns keys dropped."""
        now = self.now_fn()
        cutoff = now - self.window_s
        stale = [k for k, bucket in self._hits.items() if not bucket or bucket[-1] < cutoff]
        for k in stale:
            del self._hits[k]
        return len(stale)

    def reset(self) -> None:
        """Clear all tracked keys (test helper)."""
        self._hits.clear()


@dataclass
class CompositeRateLimiter:
    """Applies two RateLimiters (e.g. per-IP, per-session-token); both must allow."""

    per_ip: RateLimiter
    per_session: RateLimiter

    def allow(self, *, ip_key: str, session_key: str) -> bool:
        ip_ok = self.per_ip.allow(ip_key)
        session_ok = self.per_session.allow(session_key)
        return ip_ok and session_ok

    def sweep(self) -> None:
        self.per_ip.sweep()
        self.per_session.sweep()
