"""Test-only event factories: build distinct, valid contract events.

`doorboard_contracts.examples.example_event` is great for schema round-trip
tests but always uses the same fixed `event_id`/`trace_id` — fine for
testing one type in isolation, useless for ingest tests that need several
distinct, independently-idempotent events. This module generates fresh
UUIDv7 ids per call instead.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from doorboard_contracts.events import EVENT_TYPE_TO_MODEL
from doorboard_contracts.examples import PAYLOADS


def uuid7() -> UUID:
    unix_ts_ms = int(time.time() * 1000) & 0xFFFF_FFFF_FFFF
    rand_bytes = int.from_bytes(os.urandom(10), "big")
    rand_a = (rand_bytes >> 68) & 0xFFF
    rand_b = rand_bytes & 0x3FFF_FFFF_FFFF_FFFF
    hi = (unix_ts_ms << 16) | (0x7 << 12) | rand_a
    lo = (0b10 << 62) | rand_b
    return UUID(int=(hi << 64) | lo)


def build_event(
    event_type: str,
    *,
    door_id: str = "primary",
    payload_overrides: dict[str, Any] | None = None,
    monotonic_ms: int = 1_000,
) -> dict:
    model: Any = EVENT_TYPE_TO_MODEL[event_type]
    payload = dict(PAYLOADS[event_type])
    if payload_overrides:
        payload.update(payload_overrides)

    instance = model(
        event_id=uuid7(),
        type=event_type,
        source="test",
        occurred_at=datetime.now(UTC),
        monotonic_ms=monotonic_ms,
        door_id=door_id,
        trace_id=uuid7(),
        payload=payload,
    )
    return instance.model_dump(mode="json")
