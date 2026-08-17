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

    # ── display path (ADR-0018 §3) ────────────────────────────────────────
    # door-api owns the kiosk WebSocket and the session state machine, so identity
    # events must reach it or the wallboard/doorpad greeting never renders. An empty
    # token disables forwarding: mock/CI stay silent, and the ESP32 profile push and
    # identity cache behave identically either way.
    door_api_base_url: str = Field(default="http://127.0.0.1:8080", alias="DOOR_API_BASE_URL")
    door_api_internal_token: str = Field(default="", alias="DOOR_API_INTERNAL_EVENT_TOKEN")
    # Bounded tightly: this drains the recognition loop's queue, and a greeting that
    # arrives late is worth less than a frame that arrives on time.
    event_forward_timeout_s: float = Field(
        default=1.0, alias="VISIOND_EVENT_FORWARD_TIMEOUT_S", gt=0
    )

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

    # ── arrival log (ADR-0018) ────────────────────────────────────────────
    # Sightings closer together than the merge window extend one visit rather
    # than opening another (E-22), so someone lingering is one arrival.
    visit_merge_window_s: float = Field(default=600.0, alias="VISIOND_VISIT_MERGE_WINDOW_S", gt=0)
    # How often a sighting may reach SQLite, per person. Defaults to the greeting
    # cooldown: a visit log needs no finer resolution than that.
    visit_write_interval_ms: int = Field(
        default=30_000, alias="VISIOND_VISIT_WRITE_INTERVAL_MS", ge=0
    )

    # ── remote enrollment relay (ADR-0016) ────────────────────────────────
    # Empty base URL disables remote enrollment entirely: the worker never
    # starts, no key is published, and the at-door flow is unaffected.
    relay_base_url: str = Field(default="", alias="VISIOND_RELAY_BASE_URL")
    relay_device_token: str = Field(default="", alias="VISIOND_RELAY_DEVICE_TOKEN")
    # Public origin used to build invite URLs for the QR code; defaults to the
    # API base when unset (they are normally the same deployment).
    relay_public_url: str = Field(default="", alias="VISIOND_RELAY_PUBLIC_URL")
    relay_poll_interval_s: float = Field(default=5.0, alias="VISIOND_RELAY_POLL_INTERVAL_S", gt=0)
    relay_timeout_s: float = Field(default=5.0, alias="VISIOND_RELAY_TIMEOUT_S", gt=0)
    relay_backoff_max_s: float = Field(default=300.0, alias="VISIOND_RELAY_BACKOFF_MAX_S", ge=1)
    relay_invite_ttl_s: float = Field(default=3600.0, alias="VISIOND_RELAY_INVITE_TTL_S", gt=0)
    # Retired sealing keys are kept only long enough for bundles already in the
    # relay (15-min TTL) to still open, then deleted (E-12).
    relay_retired_key_ttl_s: float = Field(
        default=3600.0, alias="VISIOND_RELAY_RETIRED_KEY_TTL_S", ge=900
    )
    relay_max_images: int = Field(default=5, alias="VISIOND_RELAY_MAX_IMAGES", ge=1, le=15)

    # ── self-service enrollment (ADR-0019) ────────────────────────────────
    # A visitor at the doorpad can mint their own invite with no admin credential.
    # Presence at the door is the authorization, exactly as it already is for the
    # bell and the guestbook; these two caps are what stop a passer-by loading the
    # encrypted volume with strangers' biometrics. 0 disables self-service.
    self_enroll_per_hour: int = Field(default=6, alias="VISIOND_SELF_ENROLL_PER_HOUR", ge=0)
    self_enroll_max_enrolled: int = Field(
        default=50, alias="VISIOND_SELF_ENROLL_MAX_ENROLLED", ge=0
    )

    # How long to wait before retrying the real vision backend after three
    # consecutive frame failures dropped it to `disabled`. The frames come from
    # door-media over HTTP, so the usual cause is that service restarting — which used
    # to stop recognition permanently on an otherwise healthy door. A permanent fault
    # simply re-degrades on the next frame, so retrying costs one attempt.
    backend_recovery_delay_s: float = Field(
        default=15.0, alias="VISIOND_BACKEND_RECOVERY_DELAY_S", gt=0
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
    # The dedicated recognition camera's frames (ADR-0023), used only in
    # `VISION_MODE=dual-camera`. door-media 404s this when the door has no second camera,
    # which is what makes the mode fail loudly instead of silently reading the visitor
    # camera — before ADR-0023 `dual-camera` was accepted and behaved exactly like
    # `single-camera`, so a door could be configured for two cameras and use one.
    recognition_snapshot_url: str = Field(
        default="http://127.0.0.1:8082/snapshot/recognition",
        alias="VISIOND_RECOGNITION_SNAPSHOT_URL",
    )

    @field_validator("vision_mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v not in _ALLOWED_MODES:
            msg = f"VISION_MODE must be one of {sorted(_ALLOWED_MODES)}, got {v!r}"
            raise ValueError(msg)
        return v

    @property
    def face_snapshot_url(self) -> str:
        """Where face frames come from, per mode.

        `dual-camera` reads the recognition camera; every other hardware mode reads the
        visitor stream. One property so the backend, the health surface and the startup
        check cannot disagree about which camera is in use.
        """
        if self.vision_mode == "dual-camera":
            return self.recognition_snapshot_url
        return self.snapshot_url

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
    def relay_key_path(self) -> Path:
        """Door sealing keypair — on the encrypted enrollment volume (ADR-0016 §3)."""
        return self.enrollment_root / "relay" / "door_key.json"

    @property
    def relay_enabled(self) -> bool:
        return bool(self.relay_base_url and self.relay_device_token)

    @property
    def event_forwarding_enabled(self) -> bool:
        return bool(self.door_api_base_url and self.door_api_internal_token)

    @property
    def relay_invite_base_url(self) -> str:
        return (self.relay_public_url or self.relay_base_url).rstrip("/")

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
