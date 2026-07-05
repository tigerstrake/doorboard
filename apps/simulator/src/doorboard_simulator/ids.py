from __future__ import annotations

from uuid import UUID

TRACE_ID = UUID("9d1e8a34-52a1-4b0e-9c0f-2a7d1f66b100")
SESSION_ID = UUID("0f7e3f6f-6d69-44f7-b2e0-20f520cb04f5")
RECORDING_ID = UUID("c0a8b1d2-0f0e-4a6b-b111-5a4e2f9b7788")


def uuid7(counter: int) -> UUID:
    if counter < 1:
        msg = "counter must be positive"
        raise ValueError(msg)
    return UUID(f"018f6b2e-7c9a-7000-8000-{counter:012x}")
