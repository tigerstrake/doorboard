"""Versioned config bundle distributed by control-plane-api to the door Pi.

The bundle is the *only* config surface the NUC pushes to the Pi (the Pi
pulls it over `/config/door/{door_id}`, ADR-0002 trust model). It carries
non-secret operational settings only — vision mode, feature flags, cooldown
tuning. Nothing here may ever be a credential, DSN, or admin token; that is
enforced both by the model shape (no field is typed to hold one) and by
`assert_secret_free`, a denylist scan control-plane-api runs against every
bundle it serves as a defense-in-depth check, not the only check.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

# Substrings (case-insensitive) that must never appear as a key or string
# value anywhere in a served bundle. Intentionally broad — a false positive
# here just means renaming a legitimate field, which is cheap. A false
# negative would leak a credential to a device that can be physically stolen.
SECRET_DENYLIST: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "passwd",
    "dsn",
    "credential",
    "private_key",
    "api_key",
    "apikey",
)


class DoorConfigSettings(BaseModel):
    """Whitelisted, non-secret settings the Pi needs. `extra="forbid"` is load-bearing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    esp32_transport: str = "uart"
    vision_mode: str = "mock"
    single_camera_mode: bool = True
    greeting_cooldown_s: int = 30
    identity_cache_ttl_s: float = 2.5
    feature_flags: dict[str, bool] = {}


class ConfigBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    door_id: str
    version: int
    generated_at: datetime
    checksum: str
    settings: DoorConfigSettings


def _canonical_json(settings: DoorConfigSettings) -> bytes:
    data: dict[str, Any] = settings.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_checksum(settings: DoorConfigSettings) -> str:
    return hashlib.sha256(_canonical_json(settings)).hexdigest()


def build_bundle(
    *, door_id: str, version: int, settings: DoorConfigSettings, generated_at: datetime
) -> ConfigBundle:
    return ConfigBundle(
        door_id=door_id,
        version=version,
        generated_at=generated_at,
        checksum=compute_checksum(settings),
        settings=settings,
    )


def verify_checksum(bundle: ConfigBundle) -> bool:
    return compute_checksum(bundle.settings) == bundle.checksum


class BundleContainsSecretError(ValueError):
    pass


def assert_secret_free(bundle: ConfigBundle) -> None:
    """Raise if any denylisted substring appears in the bundle's serialized form."""
    blob = bundle.model_dump_json().lower()
    for needle in SECRET_DENYLIST:
        if needle in blob:
            msg = (
                f"config bundle for door_id={bundle.door_id!r} contains denylisted term {needle!r}"
            )
            raise BundleContainsSecretError(msg)
