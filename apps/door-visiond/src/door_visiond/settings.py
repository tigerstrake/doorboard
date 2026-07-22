"""door-visiond service settings (environment-driven).

No secrets in code.  ``SSD_DATA_ROOT`` is mandatory storage on the SSD — a
microSD path is a review-blocking defect (ADR-0007).  The enrollment DB and
transient enrollment tmp dir live under ``<SSD_DATA_ROOT>/visiond`` (ADR-0009
§1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

VisionMode = Literal["disabled", "mock", "single-camera", "dual-camera", "hardware"]
_ALLOWED_MODES: frozenset[str] = frozenset(
    {"disabled", "mock", "single-camera", "dual-camera", "hardware"}
)

# Pinned Hailo runtime + model versions (ADR-0009 §1 / T-302 startup check).
# The device must report exactly these or the pipeline degrades to `disabled`.
# The Hailo face path (T-305) is validated on HailoRT 4.23.
PINNED_HAILO_RUNTIME: str = "4.23.0"
PINNED_MODEL_ID: str = "arcface_mobilefacenet_v1"
PINNED_MODEL_DIM: int = 512


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── identity ──────────────────────────────────────────────────────────
    door_id: str = Field(default="primary", alias="DOORBOARD_DOOR_ID")

    # ── bind ──────────────────────────────────────────────────────────────
    bind: str = Field(default="127.0.0.1:8081", alias="DOOR_VISIOND_BIND")

    # ── vision mode ───────────────────────────────────────────────────────
    vision_mode: str = Field(default="mock", alias="VISION_MODE")

    # ── storage ───────────────────────────────────────────────────────────
    ssd_data_root: Path = Field(default=Path("/mnt/ssd/doorboard"), alias="SSD_DATA_ROOT")
    enrollment_root_override: Path | None = Field(
        default=None,
        alias="VISIOND_ENROLLMENT_ROOT",
    )
    require_encrypted_enrollment: bool = Field(
        default=False,
        alias="VISIOND_REQUIRE_ENCRYPTED_STORAGE",
    )

    # ── recognition tuning (ARCHITECTURE.md §5) ───────────────────────────
    identity_cache_ttl_ms: int = Field(default=2500, alias="VISIOND_IDENTITY_TTL_MS")
    greeting_cooldown_ms: int = Field(default=30_000, alias="VISIOND_GREETING_COOLDOWN_MS")
    min_face_px: int = Field(default=80, alias="VISIOND_MIN_FACE_PX")
    match_threshold: float = Field(default=0.62, alias="VISIOND_MATCH_THRESHOLD")
    stability_window: int = Field(default=3, alias="VISIOND_STABILITY_WINDOW")
    stability_required: int = Field(default=2, alias="VISIOND_STABILITY_REQUIRED")

    # ── enrollment ────────────────────────────────────────────────────────
    min_enroll_quality: float = Field(default=0.5, alias="VISIOND_MIN_ENROLL_QUALITY")
    model_dim: int = Field(default=PINNED_MODEL_DIM, alias="VISIOND_MODEL_DIM")
    model_id: str = Field(default=PINNED_MODEL_ID, alias="VISIOND_MODEL_ID")
    hailo_runtime_version: str = Field(
        default=PINNED_HAILO_RUNTIME, alias="VISIOND_HAILO_RUNTIME_VERSION"
    )

    # ── consent (ADR-0009 §5 E-7) ─────────────────────────────────────────
    consent_version: str = Field(default="v1", alias="VISIOND_CONSENT_VERSION")
    consent_statement_path: Path | None = Field(
        default=Path("docs/policies/consent-statement.md"),
        alias="VISIOND_CONSENT_STATEMENT_PATH",
    )

    # ── admin auth ────────────────────────────────────────────────────────
    # Shared bearer secret for /enroll, /unenroll, /privacy-mode.
    # Empty closes protected routes with 503.
    admin_token: str = Field(default="", alias="DOOR_VISIOND_ADMIN_TOKEN")

    # ── durable archive purge delivery ───────────────────────────────────
    sync_base_url: str = Field(default="http://127.0.0.1:8083", alias="DOOR_SYNC_BASE_URL")
    sync_admin_token: str = Field(default="", alias="DOOR_SYNC_ADMIN_TOKEN")
    sync_timeout_s: float = Field(default=2.0, alias="DOOR_VISIOND_SYNC_TIMEOUT_S", gt=0)
    purge_worker_interval_s: float = Field(
        default=1.0,
        alias="DOOR_VISIOND_PURGE_WORKER_INTERVAL_S",
        gt=0,
    )
    purge_retry_max_s: float = Field(
        default=300.0,
        alias="DOOR_VISIOND_PURGE_RETRY_MAX_S",
        ge=1,
    )

    # ── capture cadence (mock/hardware frame pacing) ──────────────────────
    frame_interval_ms: int = Field(default=100, alias="VISIOND_FRAME_INTERVAL_MS")

    # ── Hailo face pipeline (T-305) ───────────────────────────────────────
    # Two HEFs live on the SSD: an SCRFD detector and an ArcFace recognizer.
    # Paths are only touched in hardware modes; mock/disabled never load them.
    detector_hef_path: Path = Field(
        default=Path("/mnt/ssd/doorboard/models/scrfd_2.5g.hef"),
        alias="VISIOND_DETECTOR_HEF_PATH",
    )
    recognizer_hef_path: Path = Field(
        default=Path("/mnt/ssd/doorboard/models/arcface_mobilefacenet.hef"),
        alias="VISIOND_RECOGNIZER_HEF_PATH",
    )
    # door-media owns the camera; door-visiond pulls a still over HTTP rather
    # than opening the camera itself (ADR-0007 trust boundary). The MediaMTX
    # RTSP stream (rtsp://127.0.0.1:8554/visitor) is the alternative source.
    snapshot_url: str = Field(
        default="http://127.0.0.1:8082/snapshot",
        alias="VISIOND_SNAPSHOT_URL",
    )
    snapshot_timeout_s: float = Field(
        default=2.0,
        alias="VISIOND_SNAPSHOT_TIMEOUT_S",
        gt=0,
    )

    @field_validator("vision_mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v not in _ALLOWED_MODES:
            msg = f"VISION_MODE must be one of {sorted(_ALLOWED_MODES)}, got {v!r}"
            raise ValueError(msg)
        return v

    @property
    def visiond_root(self) -> Path:
        return self.ssd_data_root / "visiond"

    @property
    def enrollment_db_path(self) -> Path:
        return self.enrollment_root / "enrollment.sqlite"

    @property
    def enroll_tmp_root(self) -> Path:
        return self.enrollment_root / "tmp"

    @property
    def enrollment_root(self) -> Path:
        return self.enrollment_root_override or self.visiond_root

    @property
    def privacy_state_path(self) -> Path:
        return self.visiond_root / "privacy_mode.json"

    @property
    def purge_outbox_path(self) -> Path:
        return self.visiond_root / "purge_outbox.sqlite"

    @property
    def host(self) -> str:
        return self.bind.split(":")[0]

    @property
    def port(self) -> int:
        return int(self.bind.split(":")[1])


_settings: Settings | None = None


def settings() -> Settings:
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings


def override_settings(s: Settings) -> None:
    """Replace the singleton — for tests only."""
    global _settings  # noqa: PLW0603
    _settings = s


def reset_settings() -> None:
    """Reset the singleton to force re-read from env — for tests only."""
    global _settings  # noqa: PLW0603
    _settings = None
