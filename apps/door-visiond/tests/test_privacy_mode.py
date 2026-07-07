"""ADR-0009 P-7 / P-8 — privacy mode kills capture at the source and persists."""

from __future__ import annotations

import asyncio

from door_visiond.clock import FakeClock
from door_visiond.embedder import MockEmbedder
from door_visiond.enrollment import ProfileSpec
from door_visiond.events import EventEmitter
from door_visiond.pipeline import BackendStatus, FrameCapture
from door_visiond.privacy_store import PrivacyStore
from door_visiond.service import PrivacyModeActiveError, VisiondService
from door_visiond.settings import Settings

from .conftest import TEST_DIM, face


class _CollectingEmitter(EventEmitter):
    def __init__(self, door_id: str) -> None:
        super().__init__(door_id)
        self.events: list = []

    def emit(self, event) -> None:  # do not touch the asyncio queue in unit tests
        self.events.append(event)


class _SpyBackend:
    """Backend whose capture is gated purely by the capturing flag (E-6)."""

    def __init__(self, capture: FrameCapture) -> None:
        self._capture = capture
        self._capturing = True
        self.capture_calls = 0

    def set_capturing(self, enabled: bool) -> None:
        self._capturing = enabled

    async def next_capture(self) -> FrameCapture | None:
        self.capture_calls += 1
        return self._capture if self._capturing else None

    def status(self) -> BackendStatus:
        return BackendStatus(mode="mock", hailo_ok=True, fps=10.0, inference_ms_p50=5.0)

    async def close(self) -> None:
        return


def _pump(svc: VisiondService, spy: _SpyBackend, n: int) -> None:
    """Drive the same steps as the production run loop, deterministically."""
    for _ in range(n):
        cap = asyncio.run(spy.next_capture())
        svc.core.tick()
        if cap is not None:
            svc.core.process_capture(cap)


def _build(settings: Settings) -> tuple[VisiondService, _SpyBackend, _CollectingEmitter]:
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(b"unknown-visitor")
    spy = _SpyBackend(face(emb))
    emitter = _CollectingEmitter(settings.door_id)
    svc = VisiondService(settings, clock=FakeClock(), backend=spy, emitter=emitter)
    return svc, spy, emitter


def test_privacy_mode_kills_capture_not_door(ssd_settings: Settings) -> None:
    """P-7: privacy freezes the frame counter (capture layer) and blocks enroll."""
    svc, spy, emitter = _build(ssd_settings)
    svc.startup()

    _pump(svc, spy, 5)
    assert svc.core.frame_count == 5  # capturing

    svc.set_privacy_mode(enabled=True, changed_by="admin")
    frozen_at = svc.core.frame_count

    _pump(svc, spy, 5)
    # Capture stopped at the source: no more frames processed.
    assert svc.core.frame_count == frozen_at

    # Enrollment is refused while privacy is active.
    try:
        svc.enroll(
            display_name="Alex",
            consent_version="v1",
            consent_confirmed=True,
            images=[b"alex-photo-bytes"],
            profile=ProfileSpec("blue_wave", "#00f", None),
        )
        raise AssertionError("enroll should have raised PrivacyModeActiveError")
    except PrivacyModeActiveError:
        pass

    # privacy_mode_changed was emitted and the service remains healthy.
    changed = [e for e in emitter.events if e.type == "vision.privacy_mode_changed"]
    assert changed and changed[-1].payload.enabled is True
    health = svc.health()
    assert health["status"] == "ok"
    assert health["mode"] == "disabled"
    assert health["privacy_enabled"] is True

    # Turning privacy off restores capture (the door path was never affected).
    svc.set_privacy_mode(enabled=False, changed_by="admin")
    _pump(svc, spy, 3)
    assert svc.core.frame_count == frozen_at + 3


def test_privacy_mode_survives_restart(ssd_settings: Settings) -> None:
    """P-8: a persisted privacy flag is applied before the first frame."""
    PrivacyStore(ssd_settings.privacy_state_path).save(enabled=True, changed_by="admin")

    svc, spy, _emitter = _build(ssd_settings)
    svc.startup()  # restore happens here, before any capture

    assert svc.privacy_enabled is True
    # No capture has occurred, and none will until the flag is cleared.
    _pump(svc, spy, 5)
    assert svc.core.frame_count == 0
