"""door-media service settings.

All configuration is read from environment variables.  No secrets are set in
code; callers rely on .env or the system environment.  SSD_DATA_ROOT is the
one mandatory path — a microSD path is a review-blocking defect (ADR-0007).
"""

from __future__ import annotations

from pathlib import Path

from doorboard_config import KindRetentionPolicy, RetentionConfig
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── identity ──────────────────────────────────────────────────────────────
    door_id: str = Field(default="primary", alias="DOORBOARD_DOOR_ID")

    # ── bind ──────────────────────────────────────────────────────────────────
    bind: str = Field(default="127.0.0.1:8082", alias="DOOR_MEDIA_BIND")

    # ── media mode ────────────────────────────────────────────────────────────
    # "mock" is the CI/dev-laptop path; "mediamtx" is the production Pi path.
    media_mode: str = Field(default="mock", alias="MEDIA_MODE")

    # ── storage ───────────────────────────────────────────────────────────────
    ssd_data_root: Path = Field(default=Path("/mnt/ssd/doorboard"), alias="SSD_DATA_ROOT")

    # Max SSD usage by door-media before stopping new recordings (bytes).
    # Default 48 GiB leaves headroom for sync queue on a 256 GiB SSD.
    max_recording_bytes: int = Field(
        default=48 * 1024**3,
        alias="DOOR_MEDIA_MAX_RECORDING_BYTES",
    )
    # Stop recording when free space drops below this (bytes). Default 4 GiB.
    min_free_bytes: int = Field(
        default=4 * 1024**3,
        alias="DOOR_MEDIA_MIN_FREE_BYTES",
    )
    # Age cap for unsynced recordings (seconds). Default 7 days.
    max_clip_age_s: int = Field(
        default=7 * 24 * 3600,
        alias="DOOR_MEDIA_MAX_CLIP_AGE_S",
    )

    # ── retention per-kind ───────────────────────────────────────────────────
    bell_clip_max_age_s: int = Field(
        default=3 * 24 * 3600,
        alias="DOOR_MEDIA_BELL_CLIP_MAX_AGE_S",
    )
    bell_clip_max_size_bytes: int = Field(
        default=10 * 1024**3,
        alias="DOOR_MEDIA_BELL_CLIP_MAX_SIZE_BYTES",
    )

    video_message_max_age_s: int = Field(
        default=14 * 24 * 3600,
        alias="DOOR_MEDIA_VIDEO_MESSAGE_MAX_AGE_S",
    )
    video_message_max_size_bytes: int = Field(
        default=30 * 1024**3,
        alias="DOOR_MEDIA_VIDEO_MESSAGE_MAX_SIZE_BYTES",
    )

    photo_booth_max_age_s: int = Field(
        default=7 * 24 * 3600,
        alias="DOOR_MEDIA_PHOTO_BOOTH_MAX_AGE_S",
    )
    photo_booth_max_size_bytes: int = Field(
        default=8 * 1024**3,
        alias="DOOR_MEDIA_PHOTO_BOOTH_MAX_SIZE_BYTES",
    )

    # ── MediaMTX ──────────────────────────────────────────────────────────────
    mediamtx_api: str = Field(default="http://127.0.0.1:9997", alias="MEDIAMTX_API")
    mediamtx_bin: str = Field(default="mediamtx", alias="MEDIAMTX_BIN")
    mediamtx_config_path: Path = Field(
        default=Path("/mnt/ssd/doorboard/mediamtx/mediamtx.yml"),
        alias="MEDIAMTX_CONFIG_PATH",
    )
    # Pinned MediaMTX version for health assertion
    mediamtx_version: str = Field(default="1.9.3", alias="MEDIAMTX_VERSION")

    # ── camera ────────────────────────────────────────────────────────────────
    visitor_cam_stream: str = Field(
        default="visitor",
        alias="VISITOR_CAM_STREAM",
    )
    # rpicam-vid segment length (seconds) for the rolling recording buffer.
    segment_s: int = Field(default=2, alias="DOOR_MEDIA_SEGMENT_S")
    # Raw MediaMTX segments are a rolling buffer, not durable recordings.
    # Keep enough history to finalize long interactions, then prune them so
    # continuous capture cannot consume the SSD indefinitely.
    segment_retention_s: int = Field(
        default=60 * 60,
        alias="DOOR_MEDIA_SEGMENT_RETENTION_S",
        ge=60,
    )
    segment_cleanup_interval_s: int = Field(
        default=60,
        alias="DOOR_MEDIA_SEGMENT_CLEANUP_INTERVAL_S",
        ge=1,
    )
    max_active_recordings: int = Field(
        default=8,
        alias="DOOR_MEDIA_MAX_ACTIVE_RECORDINGS",
        ge=1,
    )

    # ── admin auth ────────────────────────────────────────────────────────────
    # Shared secret for /recordings admin endpoints. Empty = admin auth disabled
    # (acceptable in dev; CI uses empty).
    admin_token: str = Field(default="", alias="DOOR_MEDIA_ADMIN_TOKEN")

    # ── explicit photo booth ─────────────────────────────────────────────────
    feature_photobooth: bool = Field(default=False, alias="FEATURE_PHOTOBOOTH")
    photo_review_ttl_s: int = Field(default=300, alias="DOOR_MEDIA_PHOTO_REVIEW_TTL_S")
    photo_review_max_pending: int = Field(default=16, alias="DOOR_MEDIA_PHOTO_REVIEW_MAX_PENDING")

    # ── storage status broadcast interval ────────────────────────────────────
    storage_status_interval_s: int = Field(
        default=60,
        alias="DOOR_MEDIA_STORAGE_STATUS_INTERVAL_S",
    )

    @field_validator("media_mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        allowed = {"mock", "mediamtx"}
        if v not in allowed:
            msg = f"MEDIA_MODE must be one of {allowed}, got {v!r}"
            raise ValueError(msg)
        return v

    @property
    def recordings_root(self) -> Path:
        return self.ssd_data_root / "recordings"

    @property
    def segments_root(self) -> Path:
        return self.ssd_data_root / "segments"

    @property
    def thumbnails_root(self) -> Path:
        return self.ssd_data_root / "thumbnails"

    @property
    def host(self) -> str:
        return self.bind.split(":")[0]

    @property
    def port(self) -> int:
        return int(self.bind.split(":")[1])

    @property
    def retention(self) -> RetentionConfig:
        return RetentionConfig(
            min_free_bytes=self.min_free_bytes,
            max_recording_bytes=self.max_recording_bytes,
            bell_clip=KindRetentionPolicy(
                max_age_s=self.bell_clip_max_age_s,
                max_size_bytes=self.bell_clip_max_size_bytes,
            ),
            video_message=KindRetentionPolicy(
                max_age_s=self.video_message_max_age_s,
                max_size_bytes=self.video_message_max_size_bytes,
            ),
            photo_booth=KindRetentionPolicy(
                max_age_s=self.photo_booth_max_age_s,
                max_size_bytes=self.photo_booth_max_size_bytes,
            ),
        )


def get_settings() -> Settings:
    """Return a Settings instance, honouring UV_PROJECT_ENVIRONMENT patterns."""
    return Settings()


# Singleton used by the FastAPI app; tests may override via dependency injection.
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
    """Reset the singleton to force re-read from env — for tests only."""
    global _settings  # noqa: PLW0603
    _settings = None
