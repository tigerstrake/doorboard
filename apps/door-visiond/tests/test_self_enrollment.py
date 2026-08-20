"""T-312 / ADR-0019 — a visitor at the doorpad enrolling themselves.

The interesting part is not that it works; it is what stops it working. Presence at
the door is the only authorization, so the caps and the name-collision rule are the
whole safety story and each one is pinned here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from door_visiond.clock import FakeClock
from door_visiond.enrollment import DisplayNameTakenError, ProfileSpec
from door_visiond.service import SelfEnrollClosedError, VisiondService
from door_visiond.settings import Settings

from .conftest import CONSENT_VERSION


@pytest.fixture
def self_enroll_settings(ssd_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("VISIOND_RELAY_BASE_URL", "https://enroll.example.test")
    monkeypatch.setenv("VISIOND_RELAY_DEVICE_TOKEN", "device-token")
    monkeypatch.setenv("SSD_DATA_ROOT", str(ssd_settings.ssd_data_root))
    monkeypatch.setenv("VISION_MODE", "mock")
    monkeypatch.setenv("VISIOND_MODEL_DIM", str(ssd_settings.model_dim))
    return Settings()


def _svc(settings: Settings) -> VisiondService:
    return VisiondService(settings, clock=FakeClock())


def _enrol(svc: VisiondService, name: str) -> None:
    """Enrol *name* through the same method both real paths use."""
    svc.enroll(
        display_name=name,
        consent_version=CONSENT_VERSION,
        consent_confirmed=True,
        images=[b"face-of-" + name.encode()],
        profile=ProfileSpec(profile_id=f"profile_{name.lower()}", color="#0000ff", sound=None),
    )


# ---------------------------------------------------------------------------
# Minting, and the caps that bound it
# ---------------------------------------------------------------------------


def test_visitor_mints_an_invite_with_no_credential(self_enroll_settings: Settings) -> None:
    svc = _svc(self_enroll_settings)
    invite = svc.create_self_enroll_invite()
    assert str(invite["url"]).startswith("https://enroll.example.test/e/")
    assert "#k=" in str(invite["url"]), "the key fingerprint must be in the URL (E-10)"


def test_self_service_invites_are_labelled_so_the_cap_can_see_them(
    self_enroll_settings: Settings,
) -> None:
    """The hourly cap counts by label, so owner-minted invites must not consume it."""
    svc = _svc(self_enroll_settings)
    svc.create_invite(label="Tiger's phone")
    svc.create_self_enroll_invite()
    labels = [i["label"] for i in svc.list_invites(include_closed=True)]
    assert labels.count(VisiondService.SELF_ENROLL_LABEL) == 1


def test_hourly_cap_refuses_the_seventh_invite(self_enroll_settings: Settings) -> None:
    svc = _svc(self_enroll_settings)
    for _ in range(self_enroll_settings.self_enroll_per_hour):
        svc.create_self_enroll_invite()

    with pytest.raises(SelfEnrollClosedError) as caught:
        svc.create_self_enroll_invite()
    assert caught.value.reason == "rate_limited"
    assert caught.value.retry_after_s == 3600


def test_owner_minted_invites_do_not_consume_the_visitor_allowance(
    self_enroll_settings: Settings,
) -> None:
    svc = _svc(self_enroll_settings)
    for i in range(10):
        svc.create_invite(label=f"owner-{i}")
    # The owner has minted well past the visitor cap; a visitor is still served.
    assert svc.create_self_enroll_invite()["invite_id"]


def test_the_cap_survives_a_restart(self_enroll_settings: Settings) -> None:
    """Counted from the invite table, not memory: restarting must not reset it.

    Otherwise the cap is only as strong as the service's uptime.
    """
    svc = _svc(self_enroll_settings)
    for _ in range(self_enroll_settings.self_enroll_per_hour):
        svc.create_self_enroll_invite()

    reborn = _svc(self_enroll_settings)
    with pytest.raises(SelfEnrollClosedError) as caught:
        reborn.create_self_enroll_invite()
    assert caught.value.reason == "rate_limited"


def test_the_allowance_returns_once_the_hour_passes(self_enroll_settings: Settings) -> None:
    svc = _svc(self_enroll_settings)
    for _ in range(self_enroll_settings.self_enroll_per_hour):
        svc.create_self_enroll_invite()

    # Age every invite past the window rather than waiting an hour.
    stale = (datetime.now(UTC) - timedelta(hours=1, minutes=1)).isoformat()
    store = svc._store
    with store._lock:
        store._conn.execute("UPDATE relay_invite SET created_at = ?", (stale,))
        store._conn.commit()

    assert svc.create_self_enroll_invite()["invite_id"]


def test_a_full_door_says_so_rather_than_rate_limiting(
    ssd_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VISIOND_RELAY_BASE_URL", "https://enroll.example.test")
    monkeypatch.setenv("VISIOND_RELAY_DEVICE_TOKEN", "device-token")
    monkeypatch.setenv("SSD_DATA_ROOT", str(ssd_settings.ssd_data_root))
    monkeypatch.setenv("VISION_MODE", "mock")
    monkeypatch.setenv("VISIOND_MODEL_DIM", str(ssd_settings.model_dim))
    monkeypatch.setenv("VISIOND_SELF_ENROLL_MAX_ENROLLED", "1")
    svc = _svc(Settings())

    _enrol(svc, "Ford")
    with pytest.raises(SelfEnrollClosedError) as caught:
        svc.create_self_enroll_invite()
    assert caught.value.reason == "door_full"
    assert caught.value.retry_after_s is None, "waiting does not help; only the owner can"


def test_zero_per_hour_disables_self_service(
    ssd_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VISIOND_RELAY_BASE_URL", "https://enroll.example.test")
    monkeypatch.setenv("VISIOND_RELAY_DEVICE_TOKEN", "device-token")
    monkeypatch.setenv("SSD_DATA_ROOT", str(ssd_settings.ssd_data_root))
    monkeypatch.setenv("VISION_MODE", "mock")
    monkeypatch.setenv("VISIOND_MODEL_DIM", str(ssd_settings.model_dim))
    monkeypatch.setenv("VISIOND_SELF_ENROLL_PER_HOUR", "0")
    svc = _svc(Settings())

    with pytest.raises(SelfEnrollClosedError) as caught:
        svc.create_self_enroll_invite()
    assert caught.value.reason == "disabled"
    # The owner's own path is untouched by the visitor switch.
    assert svc.create_invite(label="owner")["invite_id"]


def test_privacy_mode_refuses_self_service(self_enroll_settings: Settings) -> None:
    from door_visiond.service import PrivacyModeActiveError

    svc = _svc(self_enroll_settings)
    svc.set_privacy_mode(enabled=True, changed_by="admin")
    with pytest.raises(PrivacyModeActiveError):
        svc.create_self_enroll_invite()


def test_the_owner_can_see_that_people_added_themselves(
    self_enroll_settings: Settings,
) -> None:
    """ADR-0019 §4: consenting to self-service cannot mean losing sight of it.

    A minted-but-unused invite is not an enrollment, so only consumed ones count --
    otherwise the number would climb whenever someone tapped the button and wandered
    off, and stop meaning anything.
    """
    svc = _svc(self_enroll_settings)
    assert svc.health()["self_enrolled"] == 0

    svc.create_self_enroll_invite()
    assert svc.health()["self_enrolled"] == 0, "an unused invite is not a person"
    assert svc.metrics_snapshot()["self_enrolled"] == 0.0


# ---------------------------------------------------------------------------
# The name-collision rule (ADR-0019 §2)
# ---------------------------------------------------------------------------


def test_a_stranger_cannot_enrol_as_someone_already_known(
    self_enroll_settings: Settings,
) -> None:
    """The one guard here that prevents deception rather than bounding volume."""
    svc = _svc(self_enroll_settings)
    _enrol(svc, "Mom")

    with pytest.raises(DisplayNameTakenError):
        _enrol(svc, "Mom")
    assert len(svc._store.list_people()) == 1


@pytest.mark.parametrize("impostor", ["mom", "MOM", "  Mom  ", "mom "])
def test_collision_is_case_and_whitespace_insensitive(
    self_enroll_settings: Settings, impostor: str
) -> None:
    svc = _svc(self_enroll_settings)
    _enrol(svc, "Mom")
    with pytest.raises(DisplayNameTakenError):
        _enrol(svc, impostor)


def test_different_names_still_enrol(self_enroll_settings: Settings) -> None:
    svc = _svc(self_enroll_settings)
    _enrol(svc, "Mom")
    _enrol(svc, "Dad")
    assert len(svc._store.list_people()) == 2


def test_a_rejected_name_leaves_nothing_behind(self_enroll_settings: Settings) -> None:
    """The insert is one transaction: a refused name must not leave a half-person."""
    svc = _svc(self_enroll_settings)
    _enrol(svc, "Mom")
    with pytest.raises(DisplayNameTakenError):
        _enrol(svc, "mom")

    people = svc._store.list_people()
    assert len(people) == 1
    assert people[0]["display_name"] == "Mom"
    assert people[0]["profile_id"] is not None, "the surviving person kept their profile"
