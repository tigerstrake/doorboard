"""Startup Hailo compatibility check + degrade-to-disabled behaviour (T-302)."""

from __future__ import annotations

from door_visiond.clock import FakeClock
from door_visiond.compat import DetectedVersions, check_compatibility
from door_visiond.service import VisiondService
from door_visiond.settings import PINNED_HAILO_RUNTIME, PINNED_MODEL_DIM, PINNED_MODEL_ID, Settings


def test_non_hardware_modes_always_compatible() -> None:
    for mode in ("disabled", "mock"):
        result = check_compatibility(
            mode=mode,
            expected_runtime=PINNED_HAILO_RUNTIME,
            expected_model_id=PINNED_MODEL_ID,
            expected_dim=PINNED_MODEL_DIM,
        )
        assert result.ok


def test_hardware_mode_absent_runtime_not_ok() -> None:
    result = check_compatibility(
        mode="single-camera",
        expected_runtime=PINNED_HAILO_RUNTIME,
        expected_model_id=PINNED_MODEL_ID,
        expected_dim=PINNED_MODEL_DIM,
        detected=DetectedVersions(None, None, None),
    )
    assert not result.ok
    assert "not detected" in result.detail


def test_hardware_mode_wrong_version_fixture_not_ok() -> None:
    """Startup check demonstrated against a wrong-version fixture (acceptance)."""
    result = check_compatibility(
        mode="hardware",
        expected_runtime=PINNED_HAILO_RUNTIME,
        expected_model_id=PINNED_MODEL_ID,
        expected_dim=PINNED_MODEL_DIM,
        detected=DetectedVersions("3.0.0-wrong", None, None),
    )
    assert not result.ok
    assert "mismatch" in result.detail


def test_hardware_mode_matching_version_ok() -> None:
    result = check_compatibility(
        mode="hardware",
        expected_runtime=PINNED_HAILO_RUNTIME,
        expected_model_id=PINNED_MODEL_ID,
        expected_dim=PINNED_MODEL_DIM,
        detected=DetectedVersions(PINNED_HAILO_RUNTIME, PINNED_MODEL_ID, PINNED_MODEL_DIM),
    )
    assert result.ok


def test_service_degrades_hardware_to_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SSD_DATA_ROOT", str(tmp_path / "ssd"))
    monkeypatch.setenv("VISION_MODE", "hardware")  # no Hailo present in this env
    monkeypatch.setenv("DOOR_VISIOND_ADMIN_TOKEN", "")
    settings = Settings()

    svc = VisiondService(settings, clock=FakeClock())
    svc.startup()
    assert svc.effective_mode == "disabled"
    health = svc.health()
    assert health["mode"] == "disabled"
    assert health["configured_mode"] == "hardware"
    assert health["hailo_ok"] is False
    assert health["status"] == "ok"  # never blocks the door
