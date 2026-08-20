"""Door keyring and sealed-bundle opening for remote enrollment (ADR-0016 §2, §3).

The door holds one static P-256 keypair.  Phones seal to its public half with an
ephemeral keypair (ECIES); only this module can open the result.

Two properties this file is responsible for:

* **The private key never leaves the encrypted enrollment volume.**  It is stored
  as unencrypted PKCS#8 inside ``${enrollment_root}/relay/`` — which ADR-0009 §6
  option C puts on a LUKS volume whose key comes from the NUC at boot.  A stolen
  powered-off Pi therefore yields ciphertext without a key, exactly as for the
  embedding database.  Writing this file anywhere else is a review-blocking
  defect.
* **Plaintext is never logged.**  Nothing here logs image bytes, a display name,
  or an invite secret; failures log an error class and the opaque bundle id only
  (ADR-0009 E-3, ADR-0016 P-19).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from doorboard_contracts.enrollment_relay import (
    SEAL_INFO_PREFIX,
    SEAL_SUITE,
    DoorKeyPublication,
    SealedBundle,
    SealedManifest,
)

from door_visiond.logging_setup import get_logger

logger = get_logger("door_visiond.relay_seal")

_BASE62 = string.digits + string.ascii_lowercase + string.ascii_uppercase
_CURVE = ec.SECP256R1()
_KEY_FILE_MODE = 0o600
_KEY_DIR_MODE = 0o700
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_NONCE_LEN = 12
_SALT_LEN = 32


class SealError(Exception):
    """Base class for sealed-bundle failures. Reasons are never user data."""

    reason: str = "seal_error"


class UnknownDoorKeyError(SealError):
    """The bundle was sealed to a key this door does not hold (or has pruned)."""

    reason = "unknown_door_key"


class SealAuthenticationError(SealError):
    """Malformed or tampered bundle: bad point, bad nonce, or AEAD tag mismatch."""

    reason = "seal_authentication_failed"


class SealPayloadError(SealError):
    """Authenticated but structurally wrong: bad manifest, item indexes, or sizes."""

    reason = "seal_payload_invalid"


@dataclass(frozen=True)
class OpenedBundle:
    """Decrypted bundle contents. Held in memory only, for the length of one enroll."""

    manifest: SealedManifest
    images: tuple[bytes, ...]

    def __repr__(self) -> str:
        # Never render the name or the image bytes (ADR-0009 E-2 in spirit).
        return f"OpenedBundle(images={len(self.images)}, redacted)"

    __str__ = __repr__


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise SealAuthenticationError("undecodable base64url field") from exc


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _new_key_id() -> str:
    return "dky_" + "".join(secrets.choice(_BASE62) for _ in range(22))


def fingerprint_for(public_key_bytes: bytes) -> str:
    """First 16 bytes of SHA-256 over the SEC1 point, base64url (ADR-0016 §3).

    This is what the Pi bakes into the QR fragment so a client can detect a relay
    that substituted its own key (E-10).
    """
    return _b64url_encode(hashlib.sha256(public_key_bytes).digest()[:16])


@dataclass(frozen=True)
class _StoredKey:
    key_id: str
    private_key: ec.EllipticCurvePrivateKey
    created_at: str
    retired_at: str | None = None

    @property
    def public_bytes(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )


class RelayKeyring:
    """The door's sealing keys: one active, plus retired keys kept for in-flight bundles."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._keys: dict[str, _StoredKey] = {}
        self._active_key_id = ""
        self._load_or_create()

    # -- persistence -------------------------------------------------------

    def _load_or_create(self) -> None:
        if self._path.exists():
            try:
                self._read()
                return
            except (OSError, ValueError, KeyError) as exc:
                # A corrupt keyring must not silently mint a new one: bundles
                # already sealed to the old key would fail with a confusing
                # error, and an operator needs to know the volume is damaged.
                raise SealError(f"relay keyring at {self._path} is unreadable") from exc
        self._create()

    def _read(self) -> None:
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if raw.get("version") != 1:
            msg = "unsupported relay keyring version"
            raise ValueError(msg)
        keys: dict[str, _StoredKey] = {}
        for entry in raw["keys"]:
            loaded = serialization.load_pem_private_key(
                entry["private_key_pem"].encode(), password=None
            )
            if not isinstance(loaded, ec.EllipticCurvePrivateKey):
                msg = "relay keyring holds a non-EC key"
                raise ValueError(msg)
            keys[entry["key_id"]] = _StoredKey(
                key_id=entry["key_id"],
                private_key=loaded,
                created_at=entry["created_at"],
                retired_at=entry.get("retired_at"),
            )
        active = raw["active_key_id"]
        if active not in keys:
            msg = "relay keyring active_key_id is missing from keys"
            raise ValueError(msg)
        self._keys = keys
        self._active_key_id = active

    def _create(self) -> None:
        key = _StoredKey(
            key_id=_new_key_id(),
            private_key=ec.generate_private_key(_CURVE),
            created_at=datetime.now(UTC).isoformat(),
        )
        self._keys = {key.key_id: key}
        self._active_key_id = key.key_id
        self._write()
        logger.info("relay_keyring_created", extra={"door_key_id": key.key_id})

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=_KEY_DIR_MODE)
        payload = {
            "version": 1,
            "active_key_id": self._active_key_id,
            "keys": [
                {
                    "key_id": key.key_id,
                    "created_at": key.created_at,
                    "retired_at": key.retired_at,
                    "private_key_pem": key.private_key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.PKCS8,
                        encryption_algorithm=serialization.NoEncryption(),
                    ).decode(),
                }
                for key in self._keys.values()
            ],
        }
        # Write-then-rename so a crash cannot leave a truncated keyring, and set
        # the restrictive mode before any bytes land.
        tmp = self._path.with_suffix(".tmp")
        tmp.touch(mode=_KEY_FILE_MODE)
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)
        self._path.chmod(_KEY_FILE_MODE)

    # -- publication -------------------------------------------------------

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    @property
    def active_public_key(self) -> str:
        return _b64url_encode(self._keys[self._active_key_id].public_bytes)

    @property
    def fingerprint(self) -> str:
        return fingerprint_for(self._keys[self._active_key_id].public_bytes)

    def publication(self, *, consent_version: str, consent_text: str) -> DoorKeyPublication:
        return DoorKeyPublication(
            door_key_id=self._active_key_id,
            public_key=self.active_public_key,
            fingerprint=self.fingerprint,
            consent_version=consent_version,
            consent_text=consent_text,
            published_at=datetime.now(UTC),
        )

    def rotate(self) -> str:
        """Mint a new active key, retiring the current one.

        Retired keys stay openable so bundles already in flight (relay TTL is 15
        minutes) still decrypt; ``prune_retired`` removes them afterwards.
        """
        now = datetime.now(UTC).isoformat()
        previous = self._keys[self._active_key_id]
        self._keys[previous.key_id] = _StoredKey(
            key_id=previous.key_id,
            private_key=previous.private_key,
            created_at=previous.created_at,
            retired_at=now,
        )
        fresh = _StoredKey(
            key_id=_new_key_id(),
            private_key=ec.generate_private_key(_CURVE),
            created_at=now,
        )
        self._keys[fresh.key_id] = fresh
        self._active_key_id = fresh.key_id
        self._write()
        logger.info(
            "relay_keyring_rotated",
            extra={"door_key_id": fresh.key_id, "retired_key_id": previous.key_id},
        )
        return fresh.key_id

    def prune_retired(self, *, older_than_s: float) -> int:
        """Delete retired private keys past the relay TTL (E-12)."""
        now = datetime.now(UTC)
        doomed = [
            key_id
            for key_id, key in self._keys.items()
            if key.retired_at is not None
            and (now - datetime.fromisoformat(key.retired_at)).total_seconds() > older_than_s
        ]
        for key_id in doomed:
            del self._keys[key_id]
        if doomed:
            self._write()
            logger.info("relay_keyring_pruned", extra={"pruned": len(doomed)})
        return len(doomed)

    # -- opening -----------------------------------------------------------

    def open_bundle(self, bundle: SealedBundle) -> OpenedBundle:
        """Decrypt and structurally validate a sealed bundle.

        Raises a ``SealError`` subclass on any failure. Never partially returns:
        a bundle with one bad item yields nothing.
        """
        if bundle.suite != SEAL_SUITE:
            raise SealPayloadError("unsupported seal suite")
        stored = self._keys.get(bundle.door_key_id)
        if stored is None:
            raise UnknownDoorKeyError(f"no private key for {bundle.door_key_id}")

        salt = _b64url_decode(bundle.salt)
        if len(salt) != _SALT_LEN:
            raise SealAuthenticationError("salt must be 32 bytes")

        try:
            peer = ec.EllipticCurvePublicKey.from_encoded_point(
                _CURVE, _b64url_decode(bundle.ephemeral_public_key)
            )
        except ValueError as exc:
            # Covers off-curve and malformed points; the library rejects the
            # identity element, so no small-subgroup check is needed for P-256.
            raise SealAuthenticationError("invalid ephemeral public key") from exc

        shared = stored.private_key.exchange(ec.ECDH(), peer)
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=f"{SEAL_INFO_PREFIX}|{bundle.door_key_id}|{bundle.bundle_id}".encode(),
        ).derive(shared)

        plaintexts = self._decrypt_items(bundle, key)
        manifest = self._parse_manifest(plaintexts[0])
        images = tuple(plaintexts[i] for i in range(1, len(plaintexts)))

        if manifest.image_count != len(images):
            raise SealPayloadError("manifest image_count disagrees with item count")
        if any(len(image) == 0 for image in images):
            raise SealPayloadError("empty image item")
        if not images:
            raise SealPayloadError("bundle contains no images")

        return OpenedBundle(manifest=manifest, images=images)

    def _decrypt_items(self, bundle: SealedBundle, key: bytes) -> list[bytes]:
        indexes = [item.index for item in bundle.items]
        if sorted(indexes) != list(range(len(indexes))):
            raise SealPayloadError("item indexes must be contiguous from 0")
        nonces = {item.nonce for item in bundle.items}
        if len(nonces) != len(bundle.items):
            # Nonce reuse under one key breaks GCM outright; refuse rather than
            # decrypt something whose authentication guarantee is void.
            raise SealAuthenticationError("nonce reused within bundle")

        aead = AESGCM(key)
        ordered = sorted(bundle.items, key=lambda item: item.index)
        plaintexts: list[bytes] = []
        for item in ordered:
            nonce = _b64url_decode(item.nonce)
            if len(nonce) != _NONCE_LEN:
                raise SealAuthenticationError("nonce must be 12 bytes")
            aad = f"{bundle.bundle_id}:{bundle.door_key_id}:{item.index}".encode()
            try:
                plaintext = aead.decrypt(nonce, _b64url_decode(item.ciphertext), aad)
            except InvalidTag as exc:
                raise SealAuthenticationError(f"item {item.index} failed authentication") from exc
            if len(plaintext) > _MAX_IMAGE_BYTES:
                raise SealPayloadError(f"item {item.index} exceeds the size limit")
            plaintexts.append(plaintext)
        if len(plaintexts) < 2:
            raise SealPayloadError("bundle needs a manifest and at least one image")
        return plaintexts

    @staticmethod
    def _parse_manifest(raw: bytes) -> SealedManifest:
        try:
            return SealedManifest.model_validate_json(raw)
        except ValueError as exc:
            # Deliberately does not include the exception text: a manifest
            # validation error can quote the offending value, which is user data.
            raise SealPayloadError("manifest failed validation") from exc
