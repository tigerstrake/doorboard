"""Cross-language seal compatibility (ADR-0016 P-12).

The fixture here was sealed by the browser implementation
(``apps/public-relay/lib/seal.ts``, exercised through its own vitest run).  This
test opens it with the production Python code.  If the two implementations drift —
a changed HKDF ``info``, a different AAD layout, a swapped nonce length — this
fails, which is the whole point of keeping a committed vector rather than testing
each side against itself.

Regenerate after any deliberate format change:

    WRITE_SEAL_VECTOR=1 pnpm --filter @doorboard/public-relay test
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from door_visiond.relay_seal import (
    RelayKeyring,
    SealAuthenticationError,
    SealError,
    fingerprint_for,
)
from doorboard_contracts.enrollment_relay import (
    SEAL_INFO_PREFIX,
    SEAL_SUITE,
    SealedBundle,
)

from .conftest import REPO_ROOT
from .relay_helpers import b64u_decode, corrupt_item

VECTOR_PATH = REPO_ROOT / "tests" / "fixtures" / "enrollment-relay" / "ts-sealed-vector.json"


@pytest.fixture(scope="module")
def vector() -> dict[str, object]:
    if not VECTOR_PATH.exists():
        pytest.fail(
            f"missing {VECTOR_PATH}; regenerate with "
            "`WRITE_SEAL_VECTOR=1 pnpm --filter @doorboard/public-relay test`"
        )
    return json.loads(VECTOR_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def keyring(vector: dict[str, object], tmp_path: Path) -> RelayKeyring:
    """Build a keyring holding the fixture's door private key."""
    path = tmp_path / "relay" / "door_key.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "active_key_id": vector["door_key_id"],
                "keys": [
                    {
                        "key_id": vector["door_key_id"],
                        "created_at": datetime.now(UTC).isoformat(),
                        "retired_at": None,
                        "private_key_pem": vector["door_private_key_pem"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return RelayKeyring(path)


def test_format_constants_agree_across_languages(vector: dict[str, object]) -> None:
    assert vector["suite"] == SEAL_SUITE
    assert vector["info_prefix"] == SEAL_INFO_PREFIX


def test_python_opens_a_typescript_sealed_bundle(
    vector: dict[str, object], keyring: RelayKeyring
) -> None:
    bundle = SealedBundle.model_validate(vector["bundle"])
    opened = keyring.open_bundle(bundle)

    expected = vector["expected_manifest"]
    assert isinstance(expected, dict)
    assert opened.manifest.display_name == expected["display_name"]
    assert opened.manifest.invite_secret == expected["invite_secret"]
    assert opened.manifest.consent_version == expected["consent_version"]
    assert opened.manifest.consent_confirmed is True
    assert opened.manifest.profile.profile_id == expected["profile"]["profile_id"]  # type: ignore[index]

    images = vector["expected_images_utf8"]
    assert isinstance(images, list)
    assert [image.decode() for image in opened.images] == images


def test_public_key_and_fingerprint_agree(vector: dict[str, object], keyring: RelayKeyring) -> None:
    """The QR fragment the Pi generates must match what the browser computes (E-10)."""
    assert keyring.active_public_key == vector["door_public_key"]
    assert keyring.fingerprint == vector["door_fingerprint"]
    assert (
        fingerprint_for(b64u_decode(str(vector["door_public_key"]))) == vector["door_fingerprint"]
    )


@pytest.mark.parametrize("field", ["nonce", "ciphertext"])
def test_tampering_with_the_vector_fails_authentication(
    vector: dict[str, object], keyring: RelayKeyring, field: str
) -> None:
    bundle = SealedBundle.model_validate(vector["bundle"])
    with pytest.raises(SealAuthenticationError):
        keyring.open_bundle(corrupt_item(bundle, index=1, field=field))


def test_renumbering_an_item_is_rejected(vector: dict[str, object], keyring: RelayKeyring) -> None:
    """Renumbering trips the contiguity check; a swap within range trips the AAD."""
    bundle = SealedBundle.model_validate(vector["bundle"])
    with pytest.raises(SealError):
        keyring.open_bundle(corrupt_item(bundle, index=1, field="index"))

    # Swap two valid indexes: contiguity still holds, so the AAD binding is what
    # catches this.
    swapped = bundle.model_copy(
        update={
            "items": [
                bundle.items[0],
                bundle.items[2].model_copy(update={"index": 1}),
                bundle.items[1].model_copy(update={"index": 2}),
            ]
        }
    )
    with pytest.raises(SealAuthenticationError):
        keyring.open_bundle(swapped)
