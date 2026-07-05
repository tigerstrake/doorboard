from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from doorboard_contracts.events import EVENT_ADAPTER, BaseEvent, DoorboardEvent

from doorboard_simulator.clock import SimClock
from doorboard_simulator.ids import TRACE_ID, uuid7


class EventFactory:
    def __init__(self, clock: SimClock, *, source: str = "doorboard-simulator") -> None:
        self._clock = clock
        self._source = source
        self._counter = 0

    def make(self, event_type: str, payload: Mapping[str, Any]) -> DoorboardEvent:
        self._counter += 1
        return EVENT_ADAPTER.validate_python(
            {
                "event_id": uuid7(self._counter),
                "type": event_type,
                "source": self._source,
                "occurred_at": self._clock.utc_now(),
                "monotonic_ms": self._clock.monotonic_ms,
                "door_id": "primary",
                "trace_id": TRACE_ID,
                "payload": dict(payload),
            }
        )


def event_to_log(event: BaseEvent) -> dict[str, Any]:
    dumped = event.model_dump(mode="json")
    return {
        "monotonic_ms": dumped["monotonic_ms"],
        "type": dumped["type"],
        "payload": dumped["payload"],
    }
