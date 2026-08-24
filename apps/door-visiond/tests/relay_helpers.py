"""Test-side sealing: what the phone's WebCrypto code does, in Python.

This mirrors ``apps/public-relay/lib/seal.ts`` step for step.  Keeping an
independent second implementation is the point — if the two drift, the
round-trip tests fail, which is ADR-0016 P-12.  It is test-only and must never
be imported by service code (the Pi only ever *opens* bundles).
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from doorboard_contracts.enrollment_relay import (
    SEAL_INFO_PREFIX,
    SealedBundle,
    SealedItem,
    SealedManifest,
    SealedProfile,
)


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64u_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_manifest(
    *,
    invite_secret: str,
    display_name: str = "Tiger",
    consent_version: str = "v2",
    image_count: int = 2,
    profile_id: str = "sunrise",
) -> SealedManifest:
    return SealedManifest(
        invite_secret=invite_secret,
        display_name=display_name,
        consent_version=consent_version,
        consent_confirmed=True,
        profile=SealedProfile(profile_id=profile_id, color="#ffb300", sound=None),
        captured_at=datetime.now(UTC),
        image_count=image_count,
    )


def seal_bundle(
    *,
    door_public_key: str,
    door_key_id: str,
    invite_id: str,
    bundle_id: str,
    manifest: SealedManifest,
    images: list[bytes],
    salt: bytes | None = None,
) -> SealedBundle:
    """Seal a manifest + images to the door's public key (ADR-0016 §2)."""
    peer = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), b64u_decode(door_public_key)
    )
    ephemeral = ec.generate_private_key(ec.SECP256R1())
    shared = ephemeral.exchange(ec.ECDH(), peer)
    salt = salt if salt is not None else os.urandom(32)
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=f"{SEAL_INFO_PREFIX}|{door_key_id}|{bundle_id}".encode(),
    ).derive(shared)

    aead = AESGCM(key)
    plaintexts = [manifest.model_dump_json().encode(), *images]
    items: list[SealedItem] = []
    for index, plaintext in enumerate(plaintexts):
        nonce = os.urandom(12)
        aad = f"{bundle_id}:{door_key_id}:{index}".encode()
        items.append(
            SealedItem(
                index=index,
                nonce=b64u(nonce),
                ciphertext=b64u(aead.encrypt(nonce, plaintext, aad)),
            )
        )

    return SealedBundle(
        bundle_id=bundle_id,
        invite_id=invite_id,
        door_key_id=door_key_id,
        ephemeral_public_key=b64u(
            ephemeral.public_key().public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )
        ),
        salt=b64u(salt),
        items=items,
    )


def corrupt_item(bundle: SealedBundle, *, index: int, field: str) -> SealedBundle:
    """Flip one byte of an item's nonce or ciphertext, or renumber its index."""
    raw = json.loads(bundle.model_dump_json())
    item = raw["items"][index]
    if field == "index":
        item["index"] = item["index"] + 1
    else:
        decoded = bytearray(b64u_decode(item[field]))
        decoded[0] ^= 0x01
        item[field] = b64u(bytes(decoded))
    return SealedBundle.model_validate(raw)
