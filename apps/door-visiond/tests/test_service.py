"""VisiondService: enrollment lifecycle, consent/quality gates, and P-9 tmp transience."""

from __future__ import annotations

import pytest
from door_visiond.clock import FakeClock
from door_visiond.embedder import MockEmbedder
from door_visiond.embedding import Embedding
from door_visiond.enrollment import ProfileSpec
from door_visiond.service import (
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
