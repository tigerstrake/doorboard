"""Sealed-bundle crypto: ADR-0016 P-12 (round-trip and tamper rejection).

Every tamper case must fail *closed*: no partial plaintext, no fallback path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from door_visiond.relay_seal import (
    RelayKeyring,
    SealAuthenticationError,
    SealError,
    SealPayloadError,
    UnknownDoorKeyError,
    fingerprint_for,
)
from door_visiond.settings import Settings

from .relay_helpers import b64u, b64u_decode, corrupt_item, make_manifest, seal_bundle

_IMAGES = [b"\xff\xd8\xff-image-one-payload", b"\xff\xd8\xff-image-two-payload"]


def _keyring(tmp_path: Path) -> RelayKeyring:
    return RelayKeyring(tmp_path / "relay" / "door_key.json")


def _sealed(keyring: RelayKeyring, **overrides: object):
    manifest = overrides.pop("manifest", None) or make_manifest(
        invite_secret="c2VjcmV0LXZhbHVl", image_count=len(_IMAGES)
    )
    kwargs: dict[str, object] = {
        "door_public_key": keyring.active_public_key,
        "door_key_id": keyring.active_key_id,
        "invite_id": "inv_" + "a" * 22,
        "bundle_id": "bnd_" + "b" * 22,
        "manifest": manifest,
        "images": list(_IMAGES),
    }
    kwargs.update(overrides)
    return seal_bundle(**kwargs)  # type: ignore[arg-type]


def test_seal_open_round_trip(tmp_path: Path) -> None:
    keyring = _keyring(tmp_path)
    opened = keyring.open_bundle(_sealed(keyring))
    assert opened.manifest.display_name == "Tiger"
    assert opened.manifest.consent_confirmed is True
    assert list(opened.images) == _IMAGES


def test_opened_bundle_repr_hides_contents(tmp_path: Path) -> None:
    """A stray log/repr of an opened bundle must not print a face or a name."""
    keyring = _keyring(tmp_path)
    opened = keyring.open_bundle(_sealed(keyring))
    for rendered in (repr(opened), str(opened), f"{opened}"):
        assert "Tiger" not in rendered
        assert "image-one" not in rendered
        assert "redacted" in rendered


@pytest.mark.parametrize("field", ["nonce", "ciphertext", "index"])
@pytest.mark.parametrize("index", [0, 1])
def test_tampered_item_is_rejected(tmp_path: Path, field: str, index: int) -> None:
    keyring = _keyring(tmp_path)
    tampered = corrupt_item(_sealed(keyring), index=index, field=field)
    with pytest.raises(SealError):
        keyring.open_bundle(tampered)


def test_tampered_salt_is_rejected(tmp_path: Path) -> None:
    keyring = _keyring(tmp_path)
    raw = json.loads(_sealed(keyring).model_dump_json())
    salt = bytearray(b64u_decode(raw["salt"]))
    salt[0] ^= 0x01
    raw["salt"] = b64u(bytes(salt))
    from doorboard_contracts.enrollment_relay import SealedBundle

    with pytest.raises(SealAuthenticationError):
        keyring.open_bundle(SealedBundle.model_validate(raw))


def test_bundle_id_is_bound_into_the_seal(tmp_path: Path) -> None:
    """Replaying a bundle under a different id must fail (AAD + HKDF info binding)."""
    keyring = _keyring(tmp_path)
    raw = json.loads(_sealed(keyring).model_dump_json())
    raw["bundle_id"] = "bnd_" + "z" * 22
    from doorboard_contracts.enrollment_relay import SealedBundle

    with pytest.raises(SealAuthenticationError):
        keyring.open_bundle(SealedBundle.model_validate(raw))


def test_bundle_sealed_to_another_door_is_rejected(tmp_path: Path) -> None:
    ours = _keyring(tmp_path)
    theirs = RelayKeyring(tmp_path / "other" / "door_key.json")
    foreign = _sealed(theirs)
    with pytest.raises(UnknownDoorKeyError):
        ours.open_bundle(foreign)


def test_invalid_ephemeral_point_is_rejected(tmp_path: Path) -> None:
    keyring = _keyring(tmp_path)
    raw = json.loads(_sealed(keyring).model_dump_json())
    raw["ephemeral_public_key"] = b64u(b"\x04" + b"\x00" * 64)  # not on the curve
    from doorboard_contracts.enrollment_relay import SealedBundle

    with pytest.raises(SealAuthenticationError):
        keyring.open_bundle(SealedBundle.model_validate(raw))


def test_manifest_image_count_must_match(tmp_path: Path) -> None:
    keyring = _keyring(tmp_path)
    lying = make_manifest(invite_secret="c2VjcmV0", image_count=5)
    with pytest.raises(SealPayloadError):
        keyring.open_bundle(_sealed(keyring, manifest=lying))


def test_bundle_without_images_is_rejected(tmp_path: Path) -> None:
    keyring = _keyring(tmp_path)
    manifest = make_manifest(invite_secret="c2VjcmV0", image_count=1)
    with pytest.raises(SealPayloadError):
        keyring.open_bundle(_sealed(keyring, manifest=manifest, images=[]))


def test_duplicate_nonce_within_bundle_is_rejected(tmp_path: Path) -> None:
    """Nonce reuse voids GCM's guarantee; refuse rather than decrypt anyway."""
    keyring = _keyring(tmp_path)
    raw = json.loads(_sealed(keyring).model_dump_json())
    raw["items"][1]["nonce"] = raw["items"][0]["nonce"]
    from doorboard_contracts.enrollment_relay import SealedBundle

    with pytest.raises(SealAuthenticationError):
        keyring.open_bundle(SealedBundle.model_validate(raw))


# -- keyring persistence and rotation ---------------------------------------


def test_keyring_persists_across_reopen(tmp_path: Path) -> None:
    first = _keyring(tmp_path)
    key_id, public = first.active_key_id, first.active_public_key
    second = _keyring(tmp_path)
    assert (second.active_key_id, second.active_public_key) == (key_id, public)


def test_private_key_file_is_not_world_readable(tmp_path: Path) -> None:
    keyring = _keyring(tmp_path)
    mode = (tmp_path / "relay" / "door_key.json").stat().st_mode & 0o777
    assert mode == 0o600, f"sealing key is mode {mode:o}"
    assert keyring.active_key_id


def test_fingerprint_matches_published_key(tmp_path: Path) -> None:
    keyring = _keyring(tmp_path)
    assert keyring.fingerprint == fingerprint_for(b64u_decode(keyring.active_public_key))


def test_rotation_keeps_retired_keys_openable(tmp_path: Path) -> None:
    """A bundle already in the relay must still open after a rotation (ADR-0016 §3)."""
    keyring = _keyring(tmp_path)
    in_flight = _sealed(keyring)
    old_key_id = keyring.active_key_id

    new_key_id = keyring.rotate()
    assert new_key_id != old_key_id
    assert keyring.active_key_id == new_key_id
    assert list(keyring.open_bundle(in_flight).images) == _IMAGES


def test_pruning_deletes_retired_private_keys(tmp_path: Path) -> None:
    keyring = _keyring(tmp_path)
    in_flight = _sealed(keyring)
    keyring.rotate()

    assert keyring.prune_retired(older_than_s=0.0) == 1
    with pytest.raises(UnknownDoorKeyError):
        keyring.open_bundle(in_flight)
    # And the deletion is durable, not just in memory.
    assert RelayKeyring(tmp_path / "relay" / "door_key.json").active_key_id


def test_corrupt_keyring_refuses_to_silently_remint(tmp_path: Path) -> None:
    path = tmp_path / "relay" / "door_key.json"
    _keyring(tmp_path)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SealError):
        RelayKeyring(path)


def test_keyring_lives_under_the_enrollment_root(tmp_path: Path, ssd_settings: Settings) -> None:
    """The sealing key must land on the encrypted enrollment volume (ADR-0016 §3)."""
    assert ssd_settings.relay_key_path.parent.parent == ssd_settings.enrollment_root
    assert "visiond" in str(ssd_settings.relay_key_path)
