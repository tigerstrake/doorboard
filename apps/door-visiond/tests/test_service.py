"""VisiondService: enrollment lifecycle, consent/quality gates, and P-9 tmp transience."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from door_visiond.clock import FakeClock
from door_visiond.embedder import MockEmbedder
from door_visiond.embedding import Embedding
from door_visiond.enrollment import ProfileSpec
from door_visiond.pipeline import BackendStatus, FrameCapture
from door_visiond.service import (
    EnrollmentLockedError,
    PrivacyModeActiveError,
    QualityTooLowError,
    StaleConsentError,
    VisiondService,
)
from door_visiond.settings import Settings

from .conftest import CONSENT_VERSION, TEST_DIM, face


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
        consent_version=CONSENT_VERSION,
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
            consent_version=CONSENT_VERSION,
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
            consent_version=CONSENT_VERSION,
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
    assert exc.value.current_version == CONSENT_VERSION


def test_enroll_rejects_unconfirmed_consent(ssd_settings: Settings) -> None:
    svc = _svc(ssd_settings)
    with pytest.raises(StaleConsentError):
        svc.enroll(
            display_name="Alex",
            consent_version=CONSENT_VERSION,
            consent_confirmed=False,
            images=[b"alex-photo-bytes"],
            profile=_profile(),
        )


def test_enroll_rejects_low_quality(ssd_settings: Settings) -> None:
    svc = _svc(ssd_settings)
    with pytest.raises(QualityTooLowError):
        svc.enroll(
            display_name="Alex",
            consent_version=CONSENT_VERSION,
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
            consent_version=CONSENT_VERSION,
            consent_confirmed=True,
            images=[b"alex-photo-bytes"],
            profile=_profile(),
        )


def test_recognition_populates_current_visitor(ssd_settings: Settings) -> None:
    svc = _svc(ssd_settings)
    svc.enroll(
        display_name="Alex",
        consent_version=CONSENT_VERSION,
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
            consent_version=CONSENT_VERSION,
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
        consent_version=CONSENT_VERSION,
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


class _FlakyBackend:
    """Fails its first `fail_times` frames, then behaves.

    Stands in for door-media restarting underneath us: the frames arrive over HTTP, so a
    sibling service bouncing raises URLError for a few seconds and then stops.
    """

    def __init__(self, *, fail_times: int) -> None:
        self.remaining_failures = fail_times
        self.closed = False
        self.captures = 0

    def set_capturing(self, enabled: bool) -> None:
        return

    async def next_capture(self):
        await asyncio.sleep(0)
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise OSError("door-media is restarting")
        self.captures += 1
        return FrameCapture(faces=(), inference_ms=1.0)

    def status(self) -> BackendStatus:
        return BackendStatus(mode="hardware", hailo_ok=True, fps=10.0, inference_ms_p50=1.0)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_a_degraded_backend_recovers_on_its_own(
    ssd_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Degradation used to be terminal, so restarting door-media stopped recognition
    permanently on an otherwise healthy door — visible only as `mode: disabled` in
    /health, and curable only by noticing. Found on the door (T-321)."""
    monkeypatch.setenv("VISION_MODE", "hardware")
    monkeypatch.setenv("VISIOND_BACKEND_RECOVERY_DELAY_S", "0.01")
    settings = Settings()

    failing = _FailingBackend()
    healthy = _FlakyBackend(fail_times=0)
    svc = VisiondService(
        settings,
        backend=failing,
        backend_factory=lambda: healthy,  # type: ignore[arg-type]
    )
    await svc.start()
    try:
        for _ in range(200):
            if svc.effective_mode == "disabled":
                break
            await asyncio.sleep(0.01)
        assert svc.effective_mode == "disabled"

        # ...and then comes back without anybody restarting the service.
        for _ in range(400):
            if svc.effective_mode == "hardware":
                break
            await asyncio.sleep(0.01)
        assert svc.effective_mode == "hardware"
        assert svc.health()["runtime_warning"] is None
        assert svc.metrics_snapshot()["backend_recoveries"] == 1
    finally:
        await svc.stop()


@pytest.mark.anyio
async def test_a_permanently_broken_backend_re_degrades(
    ssd_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrying is safe because a real fault simply fails the next frame too."""
    monkeypatch.setenv("VISION_MODE", "hardware")
    monkeypatch.setenv("VISIOND_BACKEND_RECOVERY_DELAY_S", "0.01")
    settings = Settings()

    svc = VisiondService(
        settings,
        backend=_FailingBackend(),
        backend_factory=_FailingBackend,  # type: ignore[arg-type]
    )
    await svc.start()
    try:
        for _ in range(400):
            if svc.metrics_snapshot()["backend_degradations"] >= 2:
                break
            await asyncio.sleep(0.01)
        # It flaps rather than wedging, and the flapping is countable in /metrics.
        assert svc.metrics_snapshot()["backend_degradations"] >= 2
        assert svc.effective_mode == "disabled"
    finally:
        await svc.stop()


def test_single_camera_reads_the_visitor_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_MODE", "single-camera")
    settings = Settings()

    assert settings.face_snapshot_url == settings.snapshot_url


def test_dual_camera_reads_the_recognition_camera(monkeypatch: pytest.MonkeyPatch) -> None:
    """`dual-camera` used to be indistinguishable from `single-camera` (ADR-0023).

    It was in the allowed-modes set and nothing plumbed a second camera anywhere, so a
    door configured for two cameras quietly used one, with no signal of any kind.
    """
    monkeypatch.setenv("VISION_MODE", "dual-camera")
    settings = Settings()

    assert settings.face_snapshot_url == settings.recognition_snapshot_url
    assert settings.face_snapshot_url != settings.snapshot_url
    assert settings.face_snapshot_url.endswith("/snapshot/recognition")


def test_hardware_mode_still_reads_the_visitor_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mode the door actually runs today must be untouched by ADR-0023."""
    monkeypatch.setenv("VISION_MODE", "hardware")
    settings = Settings()

    assert settings.face_snapshot_url == settings.snapshot_url


def test_health_states_which_camera_the_face_path_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Configured for two cameras, using one" was invisible; now it is one field."""
    monkeypatch.setenv("SSD_DATA_ROOT", str(tmp_path / "ssd"))
    monkeypatch.setenv("VISION_MODE", "dual-camera")
    monkeypatch.setenv("VISIOND_MODEL_DIM", str(TEST_DIM))
    settings = Settings()

    svc = VisiondService(settings, backend=_FlakyBackend(fail_times=0))  # type: ignore[arg-type]

    assert str(svc.health()["face_frame_source"]).endswith("/snapshot/recognition")


@pytest.mark.anyio
async def test_recovery_rebuilds_the_shared_hailo_pipeline(
    ssd_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Degrading closes the SHARED pipeline, so recovery must not hand the closed one back.

    `HardwareBackend.close()` closes the Hailo pipeline, and that object is cached by
    `_get_hailo_pipeline` and also held by the embedder. Reusing it after a degradation
    produced the worst possible outcome on the door: `vision_backend_recovered` logged,
    `mode: hardware` reported, and zero frames forever — a failure that hides itself.
    """
    monkeypatch.setenv("VISION_MODE", "hardware")
    monkeypatch.setenv("VISIOND_BACKEND_RECOVERY_DELAY_S", "0.01")
    settings = Settings()

    built: list[object] = []

    class _Pipeline:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    def _fake_pipeline() -> object:
        pipeline = _Pipeline()
        built.append(pipeline)
        return pipeline

    svc = VisiondService(
        settings,
        backend=_FailingBackend(),
        backend_factory=lambda: _FlakyBackend(fail_times=0),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(svc, "_get_hailo_pipeline", _fake_pipeline)
    svc._hailo_pipeline = _fake_pipeline()  # type: ignore[assignment]
    first = built[0]

    await svc.start()
    try:
        for _ in range(400):
            if svc.effective_mode == "hardware" and svc.metrics_snapshot()["backend_recoveries"]:
                break
            await asyncio.sleep(0.01)
        assert svc.metrics_snapshot()["backend_recoveries"] == 1
        # The cached pipeline was dropped, so the next build makes a new device rather
        # than reusing the one the degradation closed.
        assert svc._hailo_pipeline is not first
    finally:
        await svc.stop()
