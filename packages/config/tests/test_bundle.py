from datetime import UTC, datetime

import pytest
from doorboard_config import (
    BundleContainsSecretError,
    ConfigBundle,
    DoorConfigSettings,
    assert_secret_free,
    build_bundle,
    verify_checksum,
)
from pydantic import ValidationError

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)


def test_build_bundle_checksum_matches_settings() -> None:
    settings = DoorConfigSettings(vision_mode="mock")
    bundle = build_bundle(door_id="primary", version=1, settings=settings, generated_at=NOW)
    assert verify_checksum(bundle)


def test_checksum_changes_when_settings_change() -> None:
    a = build_bundle(
        door_id="primary",
        version=1,
        settings=DoorConfigSettings(vision_mode="mock"),
        generated_at=NOW,
    )
    b = build_bundle(
        door_id="primary",
        version=1,
        settings=DoorConfigSettings(vision_mode="hardware"),
        generated_at=NOW,
    )
    assert a.checksum != b.checksum


def test_tampered_settings_fail_checksum_verification() -> None:
    bundle = build_bundle(
        door_id="primary", version=1, settings=DoorConfigSettings(), generated_at=NOW
    )
    tampered = bundle.model_copy(update={"settings": DoorConfigSettings(vision_mode="hardware")})
    assert not verify_checksum(tampered)


def test_settings_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        DoorConfigSettings(nas_admin_password="hunter2")  # type: ignore[call-arg]


def test_legitimate_bundle_is_secret_free() -> None:
    bundle = build_bundle(
        door_id="primary",
        version=1,
        settings=DoorConfigSettings(feature_flags={"birdnet": True}),
        generated_at=NOW,
    )
    assert_secret_free(bundle)  # must not raise


@pytest.mark.parametrize("needle", ["token", "SECRET", "Password", "dsn", "api_key"])
def test_denylist_scan_catches_a_smuggled_secret(needle: str) -> None:
    bundle = build_bundle(
        door_id="primary",
        version=1,
        settings=DoorConfigSettings(feature_flags={f"leaked_{needle}_value": True}),
        generated_at=NOW,
    )
    with pytest.raises(BundleContainsSecretError):
        assert_secret_free(bundle)


def test_bundle_model_is_immutable() -> None:
    bundle = build_bundle(
        door_id="primary", version=1, settings=DoorConfigSettings(), generated_at=NOW
    )
    with pytest.raises(ValidationError):
        bundle.version = 2  # type: ignore[misc]


def test_bundle_rejects_unknown_top_level_fields() -> None:
    with pytest.raises(ValidationError):
        ConfigBundle(
            door_id="primary",
            version=1,
            generated_at=NOW,
            checksum="deadbeef",
            settings=DoorConfigSettings(),
            extra_field="nope",  # type: ignore[call-arg]
        )
