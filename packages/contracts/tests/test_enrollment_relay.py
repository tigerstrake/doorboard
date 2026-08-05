"""Contract-firewall tests for the relay models (ADR-0016 E-13, extending E-4).

These are structural, not behavioural: they fail CI when someone adds a field
capable of carrying plaintext biometrics or a display name across the relay, so
the invariant does not depend on a reviewer noticing.
"""

from __future__ import annotations

import typing
from datetime import UTC, datetime

import pytest
from doorboard_contracts.enrollment_relay import (
    RELAY_MODELS,
    SEAL_SUITE,
    SEALED_PLAINTEXT_MODELS,
    BundleStatus,
    DoorKeyPublication,
    InviteRegistration,
    PickupAck,
    SealedBundle,
    SealedItem,
    SealedManifest,
)
from pydantic import BaseModel, ValidationError

# Names that must never appear as a relay field: raw biometric carriers and
# nominal data. The relay sees sealed bytes and opaque ids only (ADR-0016 §1).
_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "display_name",
        "name",
        "embedding",
        "embeddings",
        "vector",
        "vectors",
        "face_crop",
        "frame",
        "image",
        "images",
        "photo",
        "photos",
        "quality",
        "person_id",
        "secret",
    }
)


def _leaf_types(annotation: object) -> set[object]:
    """Flatten an annotation to the concrete types it can hold."""
    origin = typing.get_origin(annotation)
    if origin is None:
        if isinstance(annotation, typing.TypeAliasType):
            return _leaf_types(annotation.__value__)
        return {annotation}
    leaves: set[object] = set()
    for arg in typing.get_args(annotation):
        if arg is type(None) or isinstance(arg, str):
            continue
        leaves |= _leaf_types(arg)
    return leaves


@pytest.mark.parametrize("model", RELAY_MODELS, ids=lambda m: m.__name__)
def test_no_field_can_carry_plaintext_or_a_vector(model: type[BaseModel]) -> None:
    for field_name, field in model.model_fields.items():
        assert field_name not in _FORBIDDEN_FIELD_NAMES, (
            f"{model.__name__}.{field_name} would put plaintext user data on the relay "
            f"(ADR-0016 E-13). Seal it into the manifest instead."
        )
        for leaf in _leaf_types(field.annotation):
            assert leaf is not bytes, (
                f"{model.__name__}.{field_name} is bytes; relay payloads are base64url str"
            )
            assert leaf is not float, (
                f"{model.__name__}.{field_name} accepts floats — that is an embedding-shaped "
                f"field on a relay model (ADR-0009 E-4)"
            )


@pytest.mark.parametrize("model", RELAY_MODELS, ids=lambda m: m.__name__)
def test_relay_models_forbid_extra_fields(model: type[BaseModel]) -> None:
    assert model.model_config.get("extra") == "forbid"
    assert model.model_config.get("frozen") is True


def test_sealed_plaintext_models_are_never_relay_shapes() -> None:
    """The manifest carries a name and an invite secret — it must stay inside the seal.

    If someone ever adds it to RELAY_MODELS, that would put a display name on the
    relay as a request field, which is the exact exposure ADR-0016 §1 rules out.
    """
    for model in SEALED_PLAINTEXT_MODELS:
        assert model not in RELAY_MODELS, (
            f"{model.__name__} is sealed plaintext and must not become a relay wire shape"
        )
    assert "display_name" in SealedManifest.model_fields
    assert "invite_secret" in SealedManifest.model_fields


def test_manifest_requires_affirmative_consent() -> None:
    """consent_confirmed is Literal[True] — a false value cannot be represented."""
    base = {
        "invite_secret": "c2VjcmV0",
        "display_name": "Tiger",
        "consent_version": "v2",
        "profile": {"profile_id": "warm-amber", "color": "#ffb300"},
        "captured_at": datetime.now(UTC).isoformat(),
        "image_count": 3,
    }
    assert SealedManifest.model_validate({**base, "consent_confirmed": True})
    with pytest.raises(ValidationError):
        SealedManifest.model_validate({**base, "consent_confirmed": False})


def test_sealed_bundle_round_trips_through_json() -> None:
    bundle = SealedBundle(
        bundle_id="bnd_" + "a" * 20,
        invite_id="inv_" + "b" * 20,
        door_key_id="dky_" + "c" * 20,
        ephemeral_public_key="BOb3AQ",
        salt="c2FsdA",
        items=[SealedItem(index=0, nonce="bm9uY2U", ciphertext="Y2lwaGVy")],
    )
    assert SealedBundle.model_validate_json(bundle.model_dump_json()) == bundle
    assert bundle.suite == SEAL_SUITE


def test_sealed_bundle_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        SealedBundle.model_validate(
            {
                "bundle_id": "bnd_" + "a" * 20,
                "invite_id": "inv_" + "b" * 20,
                "door_key_id": "dky_" + "c" * 20,
                "ephemeral_public_key": "BOb3AQ",
                "salt": "c2FsdA",
                "items": [{"index": 0, "nonce": "bm9uY2U", "ciphertext": "Y2lwaGVy"}],
                "display_name": "Tiger",
            }
        )


def test_base64url_rejects_padding_and_non_alphabet() -> None:
    for bad in ("Y2lwaGVy=", "with space", "plus+slash/", ""):
        with pytest.raises(ValidationError):
            SealedItem(index=0, nonce="bm9uY2U", ciphertext=bad)


def test_opaque_ids_reject_name_shaped_values() -> None:
    with pytest.raises(ValidationError):
        InviteRegistration(
            invite_id="tiger",
            secret_sha256="aGFzaA",
            expires_at=datetime.now(UTC),
        )


def test_suite_is_pinned() -> None:
    with pytest.raises(ValidationError):
        DoorKeyPublication.model_validate(
            {
                "door_key_id": "dky_" + "c" * 20,
                "suite": "rot13",
                "public_key": "BOb3AQ",
                "fingerprint": "ZmluZ2Vy",
                "consent_version": "v2",
                "consent_text": "…",
                "published_at": datetime.now(UTC).isoformat(),
            }
        )


def test_status_and_ack_reasons_are_bounded() -> None:
    """A failure reason must not become a smuggling channel for user data."""
    with pytest.raises(ValidationError):
        PickupAck(bundle_id="bnd_" + "a" * 20, outcome="failed", reason="x" * 201)
    with pytest.raises(ValidationError):
        BundleStatus(
            bundle_id="bnd_" + "a" * 20,
            status="failed",
            reason="x" * 201,
            updated_at=datetime.now(UTC),
        )
