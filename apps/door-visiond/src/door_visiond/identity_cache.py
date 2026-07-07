"""Short-lived ``current_visitor`` cache (ARCHITECTURE.md §5).

Holds at most one identity with a monotonic expiry (default TTL 2.5 s).  The
button press consumes it instantly; a miss means an immediate generic greeting.
Expiry uses the monotonic clock, never wall time.

Only presentation-safe fields are cached (opaque ``person_id`` + display name +
``profile_id``) — never an embedding or anything derived from a vector.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CurrentVisitor:
    person_id: str
    display_name: str
    profile_id: str
    expires_at_monotonic_ms: int
    expires_at_utc: datetime


class IdentityCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._visitor: CurrentVisitor | None = None

    def set(self, visitor: CurrentVisitor) -> None:
        with self._lock:
            self._visitor = visitor

    def current(self, now_monotonic_ms: int) -> CurrentVisitor | None:
        """Return the live visitor, or None if empty/expired (no side effects)."""
        with self._lock:
            v = self._visitor
            if v is None:
                return None
            if now_monotonic_ms >= v.expires_at_monotonic_ms:
                return None
            return v

    def expire_if_due(self, now_monotonic_ms: int) -> CurrentVisitor | None:
        """Clear + return the visitor iff its TTL has elapsed, else None."""
        with self._lock:
            v = self._visitor
            if v is not None and now_monotonic_ms >= v.expires_at_monotonic_ms:
                self._visitor = None
                return v
            return None

    def clear(self) -> CurrentVisitor | None:
        """Unconditionally clear; return the prior visitor (for identity_expired)."""
        with self._lock:
            v = self._visitor
            self._visitor = None
            return v

    def peek(self) -> CurrentVisitor | None:
        """Return the raw cached visitor ignoring expiry (diagnostics/tests)."""
        with self._lock:
            return self._visitor
