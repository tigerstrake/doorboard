from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

from door_visiond.adapters import VisionPipelineStatus
from doorboard_contracts import DoorboardEvent

from doorboard_simulator.clock import SimClock
from doorboard_simulator.events import EventFactory
from doorboard_simulator.outages import OutageState


@dataclass(frozen=True)
class PersonScript:
    person_id: str
    display_name: str
    profile_id: str
    confidence: float = 0.93


class FakeVisionPipeline:
    ttl_ms = 2_500
    cooldown_ms = 30_000

    def __init__(self, clock: SimClock, events: EventFactory, outages: OutageState) -> None:
        self._clock = clock
        self._events = events
        self._outages = outages
        self._queue: asyncio.Queue[DoorboardEvent] = asyncio.Queue()
        self._last_stable_mono_ms: dict[str, int] = {}

    async def face_visible(
        self, *, face_count: int = 1, largest_face_px: int = 184
    ) -> DoorboardEvent:
        event = self._events.make(
            "vision.face_visible",
            {"face_count": face_count, "largest_face_px": largest_face_px},
        )
        await self._queue.put(event)
        return event

    async def identity_stable(self, person: PersonScript) -> DoorboardEvent | None:
        last = self._last_stable_mono_ms.get(person.person_id)
        if last is not None and self._clock.monotonic_ms - last < self.cooldown_ms:
            return None
        expires_at_monotonic_ms = self._clock.monotonic_ms + self.ttl_ms
        event = self._events.make(
            "vision.identity_stable",
            {
                "person_id": person.person_id,
                "display_name": person.display_name,
                "confidence": person.confidence,
                "expires_at": self._clock.utc_now() + timedelta(milliseconds=self.ttl_ms),
                "expires_at_monotonic_ms": expires_at_monotonic_ms,
                "profile_id": person.profile_id,
            },
        )
        self._last_stable_mono_ms[person.person_id] = self._clock.monotonic_ms
        await self._queue.put(event)
        return event

    async def identity_expired(self, person_id: str) -> DoorboardEvent:
        event = self._events.make("vision.identity_expired", {"person_id": person_id})
        await self._queue.put(event)
        return event

    async def pipeline_status(self) -> DoorboardEvent:
        status = self.status()
        event = self._events.make(
            "vision.pipeline_status",
            {
                "mode": status.mode,
                "hailo_ok": status.hailo_ok,
                "fps": status.fps,
                "inference_ms_p50": status.inference_ms_p50,
            },
        )
        await self._queue.put(event)
        return event

    def events(self) -> AsyncIterator[DoorboardEvent]:
        return self._event_stream()

    async def _event_stream(self) -> AsyncIterator[DoorboardEvent]:
        while True:
            yield await self._queue.get()

    def status(self) -> VisionPipelineStatus:
        return VisionPipelineStatus(
            mode="mock",
            hailo_ok=self._outages.hailo_ok,
            fps=15.0 if self._outages.hailo_ok else 0.0,
            inference_ms_p50=22.5 if self._outages.hailo_ok else 0.0,
        )
