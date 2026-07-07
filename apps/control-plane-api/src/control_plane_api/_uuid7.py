"""Minimal UUIDv7 generator.

control-plane-api is normally an event *consumer* (`/ingest`), but the
presence engine (T-504) is the first thing here that also *emits* a
contract event on its own initiative (`status.presence_changed`, when a
resolved label changes). `doorboard_contracts.events.BaseEvent.event_id`
requires an actual UUIDv7 — this mirrors `apps/door-media/_uuid7.py` rather
than adding a library dependency for one function.

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

    rand_a = (rand_bytes >> 68) & 0xFFF  # top 12 bits of rand
    rand_b = rand_bytes & 0x3FFF_FFFF_FFFF_FFFF  # bottom 62 bits

    hi = (unix_ts_ms << 16) | (0x7 << 12) | rand_a
    lo = (0b10 << 62) | rand_b

    uuid_int = (hi << 64) | lo
    return UUID(int=uuid_int)
