"""A small in-process limiter on FAILED authentications, keyed by peer IP.

The enrollment-key release route hands out the LUKS passphrase for the door
Pi's biometric volume (ADR-0009 §6) to whoever presents its token, on a port
bound to the LAN. Before this there was no limit on guesses and a wrong token
was not even logged. `/admin/*` (a shared bearer secret) had the same exposure.

Counting failures rather than requests is deliberate: the admin UI and the
presence webhooks legitimately make dozens of authenticated calls a minute
from one address, and a limit on those would lock the owner out of their own
door; a limit on wrong tokens only slows a guesser down. After `limit`
failures inside `window_s` every attempt from that address — right token or
not — is refused until the window drains.

Bounded memory: at most `max_tracked` addresses are remembered; when that is
exceeded the address with the oldest failure is forgotten first.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class FailureRateLimiter:
    def __init__(
        self,
        *,
        limit: int,
        window_s: float = 60.0,
        max_tracked: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            msg = "limit must be at least 1"
            raise ValueError(msg)
        self._limit = limit
        self._window_s = window_s
        self._max_tracked = max_tracked
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        stamps = self._failures.setdefault(key, deque())
        cutoff = now - self._window_s
        while stamps and stamps[0] <= cutoff:
            stamps.popleft()
        if not stamps:
            self._failures.pop(key, None)
        return stamps

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, self._clock())) >= self._limit

    def record_failure(self, key: str) -> int:
        """Record one failed attempt; returns the failures now inside the window."""
        with self._lock:
            now = self._clock()
            stamps = self._prune(key, now)
            if not stamps:
                self._failures[key] = stamps
            stamps.append(now)
            if len(self._failures) > self._max_tracked:
                oldest = min(self._failures, key=lambda k: self._failures[k][0])
                self._failures.pop(oldest, None)
            return len(stamps)
