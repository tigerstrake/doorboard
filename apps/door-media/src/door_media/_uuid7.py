"""Minimal UUIDv7 generator.

UUIDv7 uses the current Unix millisecond timestamp in the high bits plus
random data in the low bits — this gives time-ordered UUIDs without a library
dependency on uuid7.

Format (RFC 9562 §5.7):
  - bits 0-47:  unix_ts_ms (48 bits)
  - bits 48-51: version = 0b0111 (4 bits)
  - bits 52-63: random_a (12 bits)
  - bits 64-65: variant = 0b10 (2 bits)
  - bits 66-127: random_b (62 bits)
"""

from __future__ import annotations

import os
import time
from uuid import UUID


def uuid7() -> UUID:
    """Return a new UUIDv7 based on the current UTC millisecond timestamp."""
    unix_ts_ms = int(time.time() * 1000) & 0xFFFF_FFFF_FFFF  # 48 bits
    rand_bytes = int.from_bytes(os.urandom(10), "big")  # 80 random bits

    # Lay out the fields
    # Bits [0..47]  = unix_ts_ms
    # Bits [48..51] = 0b0111 (version 7)
    # Bits [52..63] = rand_bytes[0..11]
    # Bits [64..65] = 0b10 (variant)
    # Bits [66..127] = rand_bytes[14..77]

    rand_a = (rand_bytes >> 68) & 0xFFF  # top 12 bits of rand
    rand_b = rand_bytes & 0x3FFF_FFFF_FFFF_FFFF  # bottom 62 bits

    hi = (unix_ts_ms << 16) | (0x7 << 12) | rand_a
    lo = (0b10 << 62) | rand_b

    uuid_int = (hi << 64) | lo
    return UUID(int=uuid_int)
