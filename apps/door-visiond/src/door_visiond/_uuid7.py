"""Minimal UUIDv7 generator (RFC 9562 §5.7).

Time-ordered UUIDs without a third-party dependency: 48-bit Unix millisecond
timestamp in the high bits, CSPRNG bits in the low bits.  Used for every
``event_id``/``trace_id`` door-visiond emits.
"""

from __future__ import annotations

import os
import time
from uuid import UUID


def uuid7() -> UUID:
    """Return a new UUIDv7 based on the current UTC millisecond timestamp."""
    unix_ts_ms = int(time.time() * 1000) & 0xFFFF_FFFF_FFFF  # 48 bits
    rand_bytes = int.from_bytes(os.urandom(10), "big")  # 80 random bits

    rand_a = (rand_bytes >> 68) & 0xFFF  # top 12 bits
    rand_b = rand_bytes & 0x3FFF_FFFF_FFFF_FFFF  # bottom 62 bits

    hi = (unix_ts_ms << 16) | (0x7 << 12) | rand_a
    lo = (0b10 << 62) | rand_b
    return UUID(int=(hi << 64) | lo)
