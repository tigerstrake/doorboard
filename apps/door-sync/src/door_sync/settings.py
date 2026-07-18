"""door-sync service settings.

Read entirely from the environment (`.env` in dev). This service runs on the
**door Pi** (medium/low trust, physically stealable — ARCHITECTURE.md §2), so
the trust model bars it from ever holding a broad credential. The only secrets
it may see are:

  - ``SYNC_INGEST_TOKEN`` — an *ingest*-scoped service token the NUC issued to
    this door (event mirror + person-purge; ADR-0009 §3.4). Revocable from the
    NUC.
  - the NAS *service* account behind ``NAS_SYNC_TARGET`` — a limited upload
    credential, never NAS admin.

It must NOT know the Postgres DSN, the NUC admin token, Home Assistant admin
secrets, or the MQTT broker password. ``tests/test_credential_fence.py`` greps
this package against a denylist to keep it that way.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from door_sync.fence import validate_roots


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # ── identity ──────────────────────────────────────────────────────────────
    door_id: str = Field(default="primary", alias="DOORBOARD_DOOR_ID")

    # ── bind ──────────────────────────────────────────────────────────────────
    bind: str = Field(default="127.0.0.1:8083", alias="DOOR_SYNC_BIND")

    # ── storage ───────────────────────────────────────────────────────────────
    # SSD only (ADR-0007). The durable queue and all media live under here; a
    # microSD path is a review-blocking defect.
    ssd_data_root: Path = Field(default=Path("/mnt/ssd/doorboard"), alias="SSD_DATA_ROOT")

    # Directories under SSD_DATA_ROOT that door-sync is allowed to upload from.
    # Biometric/identity data (visiond/) is excluded by omission — see fence.py.
    syncable_roots_raw: str = Field(default="recordings,thumbnails", alias="SYNC_SYNCABLE_ROOTS")

    # ── NUC target (control-plane-api) ────────────────────────────────────────
    control_plane_url: str = Field(default="", alias="CONTROL_PLANE_URL")
    # ingest-scoped token (event mirror + person-purge). Empty in dev/CI.
    ingest_token: str = Field(default="", alias="SYNC_INGEST_TOKEN")

    # ── NAS target (archive) ──────────────────────────────────────────────────
    # Filesystem path of the mounted NAS share (limited service account). Real
    # SFTP/rsync provisioning is deploy/nas scope (hardware-deferred); the
    # filesystem adapter is the CI/dev path and models a mounted share exactly.
    nas_sync_target: str = Field(default="", alias="NAS_SYNC_TARGET")

    # Which media target to use: "nas" (filesystem archive) or "mock" (in-proc,
    # for dev without a share). Events/purge always go to the NUC target.
    media_target: str = Field(default="mock", alias="SYNC_MEDIA_TARGET")

    # ── door-media (local, loopback) ──────────────────────────────────────────
    # Used only to (a) reconcile finalized-but-unsynced clips on startup and
    # (b) license local deletion via POST /internal/sync_completed. Loopback.
    door_media_url: str = Field(default="http://127.0.0.1:8082", alias="DOOR_MEDIA_URL")
    door_media_admin_token: str = Field(default="", alias="DOOR_MEDIA_ADMIN_TOKEN")

    # ── admin auth (local; same stopgap as door-media) ────────────────────────
    # Gates GET /queue. An empty value closes the route with 503. This is a
    # low-trust local secret for the admin UI, never a NAS/HA admin credential.
    admin_token: str = Field(default="", alias="DOOR_SYNC_ADMIN_TOKEN")

    # ── retry policy ──────────────────────────────────────────────────────────
    # Exponential backoff with full jitter, bounded. Transient (target-down)
    # failures retry forever within these bounds; only *permanent* failures
    # (4xx, checksum mismatch, missing local file) count toward the cap below.
    backoff_base_s: float = Field(default=2.0, alias="SYNC_BACKOFF_BASE_S")
    backoff_max_s: float = Field(default=300.0, alias="SYNC_BACKOFF_MAX_S")
    # Permanent failures before an item is moved to dead-letter (surfaced).
    max_permanent_attempts: int = Field(default=5, alias="SYNC_MAX_PERMANENT_ATTEMPTS")

    # Worker poll interval when the queue is idle or all items are backing off.
    poll_interval_s: float = Field(default=1.0, alias="SYNC_POLL_INTERVAL_S")

    # Completed items are pruned after this many seconds to bound queue growth
    # while still catching near-term duplicate re-deliveries. Dead-letters are
    # never auto-pruned (they need attention).
    completed_retention_s: int = Field(default=24 * 3600, alias="SYNC_COMPLETED_RETENTION_S")

    @field_validator("media_target")
    @classmethod
    def _validate_media_target(cls, v: str) -> str:
        allowed = {"nas", "mock"}
        if v not in allowed:
            msg = f"SYNC_MEDIA_TARGET must be one of {allowed}, got {v!r}"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _validate_nas_target(self) -> Settings:
        if self.media_target != "nas":
            return self
        if not self.nas_sync_target.strip():
            raise ValueError("NAS_SYNC_TARGET is required when SYNC_MEDIA_TARGET=nas")
        if not Path(self.nas_sync_target).is_absolute():
            raise ValueError("NAS_SYNC_TARGET must be an absolute path")
        return self

    @property
    def syncable_roots(self) -> tuple[str, ...]:
        return validate_roots(tuple(self.syncable_roots_raw.split(",")))

    @property
    def queue_db_path(self) -> Path:
        return self.ssd_data_root / "sync" / "queue.sqlite"

    @property
    def host(self) -> str:
        return self.bind.split(":")[0]

    @property
    def port(self) -> int:
        return int(self.bind.split(":")[1])


def get_settings() -> Settings:
    return Settings()


_settings: Settings | None = None


def settings() -> Settings:
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = get_settings()
    return _settings


def override_settings(s: Settings) -> None:
    """Replace the singleton — for tests only."""
    global _settings  # noqa: PLW0603
    _settings = s


def reset_settings() -> None:
    global _settings  # noqa: PLW0603
    _settings = None
