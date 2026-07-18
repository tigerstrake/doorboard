"""VisiondService: enrollment lifecycle, consent/quality gates, and P-9 tmp transience."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from door_visiond.clock import FakeClock
from door_visiond.embedder import MockEmbedder
from door_visiond.embedding import Embedding
from door_visiond.enrollment import ProfileSpec
from door_visiond.pipeline import BackendStatus
from door_visiond.service import (
    EnrollmentLockedError,
    PrivacyModeActiveError,
    QualityTooLowError,
    StaleConsentError,
    VisiondService,
)
from door_visiond.settings import Settings

from .conftest import TEST_DIM, face


class _RaisingEmbedder:
    model_id = "raiser"
    dim = TEST_DIM

    def embed(self, image_bytes: bytes) -> tuple[Embedding, float]:
        raise RuntimeError("injected embed failure")


class _FailingBackend:
    closed = False

    def set_capturing(self, enabled: bool) -> None:
        return

    async def next_capture(self):
        raise RuntimeError("injected backend failure")

    def status(self) -> BackendStatus:
        return BackendStatus(mode="mock", hailo_ok=False, fps=0.0, inference_ms_p50=0.0)

    async def close(self) -> None:
        self.closed = True


def _profile() -> ProfileSpec:
    return ProfileSpec(profile_id="blue_wave", color="#0000ff", sound=None)


def _svc(settings: Settings, **kw) -> VisiondService:
    svc = VisiondService(settings, clock=FakeClock(), **kw)
    svc.startup()
    return svc


def test_enroll_tmp_is_empty_after_success(ssd_settings: Settings) -> None:
    svc = _svc(ssd_settings)
    result = svc.enroll(
        display_name="Alex",
        consent_version="v1",
        consent_confirmed=True,
        images=[b"alex-photo-bytes"],
        profile=_profile(),
    )
    assert result.embeddings_created == 1
    assert list(ssd_settings.enroll_tmp_root.iterdir()) == []


def test_required_encrypted_storage_fails_closed(tmp_path: Path) -> None:
    enrollment_root = tmp_path / "unencrypted-enrollment"
    settings = Settings(
        SSD_DATA_ROOT=tmp_path / "ssd",
        VISION_MODE="mock",
        VISIOND_MODEL_DIM=TEST_DIM,
        VISIOND_ENROLLMENT_ROOT=enrollment_root,
        VISIOND_REQUIRE_ENCRYPTED_STORAGE=True,
    )
    svc = _svc(settings)
    assert svc.effective_mode == "disabled"
    assert svc.health()["enrollment_locked"] is True
    assert svc.health()["status"] == "degraded"
    assert not enrollment_root.exists()
    with pytest.raises(EnrollmentLockedError):
        svc.enroll(
            display_name="Alex",
            consent_version="v1",
            consent_confirmed=True,
            images=[b"alex-photo-bytes"],
            profile=_profile(),
        )


def test_enroll_tmp_is_empty_after_failure(ssd_settings: Settings) -> None:
    """P-9: an exception mid-enroll still wipes the transient image dir."""
    svc = _svc(ssd_settings, embedder=_RaisingEmbedder())
    with pytest.raises(RuntimeError):
        svc.enroll(
            display_name="Alex",
            consent_version="v1",
            consent_confirmed=True,
            images=[b"alex-photo-bytes"],
            profile=_profile(),
        )
    assert list(ssd_settings.enroll_tmp_root.iterdir()) == []


def test_startup_wipes_leftover_tmp(ssd_settings: Settings) -> None:
    svc = _svc(ssd_settings)
    stray = ssd_settings.enroll_tmp_root / "enroll-crashed"
    stray.mkdir(parents=True, exist_ok=True)
    (stray / "raw.bin").write_bytes(b"leftover raw face image")
    svc.startup()
    assert list(ssd_settings.enroll_tmp_root.iterdir()) == []


@pytest.mark.anyio
async def test_repeated_backend_failure_degrades_without_crash_loop(
    ssd_settings: Settings,
) -> None:
    backend = _FailingBackend()
    svc = VisiondService(ssd_settings, clock=FakeClock(), backend=backend)
    await svc.start()
    try:
        for _ in range(100):
            if svc.effective_mode == "disabled":
                break
            await asyncio.sleep(0.01)
        assert svc.effective_mode == "disabled"
        assert backend.closed
        assert svc.health()["status"] == "degraded"
        assert svc.metrics_snapshot()["pipeline_errors"] == 3
    finally:
        await svc.stop()


def test_enroll_rejects_stale_consent(ssd_settings: Settings) -> None:
    svc = _svc(ssd_settings)
    with pytest.raises(StaleConsentError) as exc:
        svc.enroll(
            display_name="Alex",
            consent_version="v0",
            consent_confirmed=True,
            images=[b"alex-photo-bytes"],
            profile=_profile(),
        )
    assert exc.value.current_version == "v1"


def test_enroll_rejects_unconfirmed_consent(ssd_settings: Settings) -> None:
    svc = _svc(ssd_settings)
    with pytest.raises(StaleConsentError):
        svc.enroll(
            display_name="Alex",
            consent_version="v1",
            consent_confirmed=False,
            images=[b"alex-photo-bytes"],
            profile=_profile(),
        )


def test_enroll_rejects_low_quality(ssd_settings: Settings) -> None:
    svc = _svc(ssd_settings)
    with pytest.raises(QualityTooLowError):
        svc.enroll(
            display_name="Alex",
            consent_version="v1",
            consent_confirmed=True,
            images=[b"aa"],  # too small → quality below threshold
            profile=_profile(),
        )


def test_enroll_blocked_during_privacy_mode(ssd_settings: Settings) -> None:
    svc = _svc(ssd_settings)
    svc.set_privacy_mode(enabled=True, changed_by="admin")
    with pytest.raises(PrivacyModeActiveError):
        svc.enroll(
            display_name="Alex",
            consent_version="v1",
            consent_confirmed=True,
            images=[b"alex-photo-bytes"],
            profile=_profile(),
        )


def test_recognition_populates_current_visitor(ssd_settings: Settings) -> None:
    svc = _svc(ssd_settings)
    svc.enroll(
        display_name="Alex",
        consent_version="v1",
        consent_confirmed=True,
        images=[b"alex-photo-bytes"],
        profile=_profile(),
    )
    # Same source bytes -> same embedding -> a match.
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(b"alex-photo-bytes")
    svc.core.process_capture(face(emb))
    svc.core.process_capture(face(emb))  # 2-of-3 stability

    visitor = svc.current_visitor()
    assert visitor is not None
    assert visitor["display_name"] == "Alex"
    assert svc.cache_hit_rate() > 0.0


def test_enroll_unenroll_churn_prunes_identity_state(ssd_settings: Settings) -> None:
    """Repeated enroll/recognize/unenroll leaves no stale per-person state."""
    svc = _svc(ssd_settings)
    for n in range(5):
        seed = f"person-{n}-face-photo".encode()
        result = svc.enroll(
            display_name=f"Person{n}",
            consent_version="v1",
            consent_confirmed=True,
            images=[seed],
            profile=ProfileSpec(profile_id=f"prof{n}", color="#00f", sound=None),
        )
        emb, _q = MockEmbedder(dim=TEST_DIM).embed(seed)
        svc.core.process_capture(face(emb))
        svc.core.process_capture(face(emb))
        svc.unenroll(result.person_id)

    assert svc.core._last_stable_ms == {}
    assert svc.core._first_seen_ms == {}
    assert svc.core._streak_trace == {}


def test_unenroll_flushes_current_visitor(ssd_settings: Settings) -> None:
    svc = _svc(ssd_settings)
    result = svc.enroll(
        display_name="Alex",
        consent_version="v1",
        consent_confirmed=True,
        images=[b"alex-photo-bytes"],
        profile=_profile(),
    )
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(b"alex-photo-bytes")
    svc.core.process_capture(face(emb))
    svc.core.process_capture(face(emb))
    assert svc.current_visitor() is not None

    out = svc.unenroll(result.person_id)
    assert out["deleted"] is True
    assert out["archive_purge"] == "queued"
    assert svc.current_visitor() is None
    assert svc.health()["archive_purge_queue_depth"] == 1


@pytest.mark.anyio
async def test_unenroll_outbox_delivers_and_clears_after_success(
    ssd_settings: Settings,
) -> None:
    ssd_settings.purge_worker_interval_s = 0.01
    svc = VisiondService(ssd_settings, clock=FakeClock())
    delivered: list[str] = []

    def record_delivery(person_id: str) -> None:
        delivered.append(person_id)

    svc._deliver_purge = record_delivery
    await svc.start()
    try:
        svc.unenroll("prs_remote_purge")
        for _ in range(50):
            if svc.health()["archive_purge_queue_depth"] == 0:
                break
            await asyncio.sleep(0.01)
        assert delivered == ["prs_remote_purge"]
        assert svc.health()["archive_purge_queue_depth"] == 0
        assert svc.metrics_snapshot()["archive_purges_delivered"] == 1
    finally:
        await svc.stop()
