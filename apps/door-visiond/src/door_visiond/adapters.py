from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from doorboard_contracts import DoorboardEvent


@dataclass(frozen=True)
class VisionPipelineStatus:
    mode: str
    hailo_ok: bool
    fps: float
    inference_ms_p50: float


class VisionPipeline(Protocol):
    def events(self) -> AsyncIterator[DoorboardEvent]:
        """Yield typed vision events from the active vision backend."""
        ...

    def status(self) -> VisionPipelineStatus:
        """Return the current vision pipeline health and mode."""
        ...
