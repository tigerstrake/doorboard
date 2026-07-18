"""control-plane-api service settings.

All configuration is read from environment variables (`.env` in dev). No
secrets are hard-coded; `.env.example` is the template. This is the trusted
NUC service (ARCHITECTURE.md §2), so unlike the door Pi services it *does*
hold real credentials (Postgres, MQTT, notification channel) — but still
never logs them (see `ntfy_topic`/`postgres_dsn` are plain `str`, not
`SecretStr`, only because nothing here logs `Settings` wholesale; if that
changes, wrap them).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── identity ─────────────────────────────────────────────────────────
    door_id: str = Field(default="primary", alias="DOORBOARD_DOOR_ID")

    # ── bind ─────────────────────────────────────────────────────────────
    bind: str = Field(default="127.0.0.1:8090", alias="CONTROL_PLANE_BIND")

    # ── database ─────────────────────────────────────────────────────────
    # Postgres in production/dev; tests point this at a disposable schema on
    # the same server (see tests/conftest.py) rather than swapping engines,
    # so the exact SQL the service runs in prod is what tests exercise.
    postgres_dsn: str = Field(
        default="postgresql+psycopg://doorboard:doorboard@localhost:5432/doorboard",
        alias="POSTGRES_DSN",
    )

    # ── MQTT audit fan-out ───────────────────────────────────────────────
    # Empty/unset disables fan-out (dev/CI default) rather than failing startup —
    # Mosquitto is control-plane-internal plumbing, not the door critical path,
    # and audit fan-out must never block ingestion (ARCHITECTURE.md §10).
    mqtt_url: str = Field(default="", alias="MQTT_URL")
    mqtt_username: str = Field(default="", alias="MQTT_USERNAME")
    mqtt_password: str = Field(default="", alias="MQTT_PASSWORD")

    # ── notifications (ntfy chosen per T-501 brief; see README) ─────────
    ntfy_url: str = Field(default="", alias="NTFY_URL")
    ntfy_topic: str = Field(default="", alias="NTFY_TOPIC")
    # Threshold for the "sync failing" alert, using media.storage_status's
    # oldest_unsynced_s as the proxy signal (see notify.py).
    sync_stall_alert_s: int = Field(default=4 * 3600, alias="CONTROL_PLANE_SYNC_STALL_ALERT_S")
    # Minimum gap between repeat notifications for the same rule+subject, so
    # a persistently bad condition doesn't page on every ingested event.
    notify_cooldown_s: int = Field(default=3600, alias="CONTROL_PLANE_NOTIFY_COOLDOWN_S")

    # ── Telegram video-message delivery (NUC-only; ADR-0012) ─────────────
    # When a visitor SAVES a video message, the clip is pulled from door-api's
    # admin media endpoint and sent to these Telegram chats. Disabled (silent
    # no-op) unless a bot token, ≥1 chat id, and door-api media creds are all
    # set — same fail-safe default as the ntfy channel above.
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    # Comma-separated chat ids — yourself, your roommate, or a shared group.
    # Plain `str` + manual split (see `telegram_chat_id_list`): pydantic-settings
    # JSON-decodes `list[str]` env vars *before* validators run, so a
    # comma-separated value would crash a real list field.
    telegram_chat_ids: str = Field(default="", alias="TELEGRAM_CHAT_IDS")
    telegram_api_base_url: str = Field(
        default="https://api.telegram.org", alias="TELEGRAM_API_BASE_URL"
    )
    # Telegram bot uploads cap at 50 MB; above this we send a text pointer instead.
    telegram_max_video_bytes: int = Field(
        default=50 * 1024 * 1024, alias="TELEGRAM_MAX_VIDEO_BYTES"
    )
    # door-api admin media source: the clip lives on the Pi; the NUC pulls it on
    # demand (the NUC is the legitimate holder of admin credentials, not the Pi).
    door_api_base_url: str = Field(default="", alias="CONTROL_PLANE_DOOR_API_BASE_URL")
    door_api_admin_token: str = Field(default="", alias="CONTROL_PLANE_DOOR_API_ADMIN_TOKEN")

    # ── admin auth (stopgap — see packages/auth README) ──────────────────
    admin_token: str = Field(default="", alias="CONTROL_PLANE_ADMIN_TOKEN")

    # ── presence engine (T-504) ──────────────────────────────────────────
    # How many `presence_history` rows to retain per subject_id — an
    # append-only label-change log grows forever otherwise. 500 rows is
    # generous for a change-only log (years, at plausible change rates)
    # while staying bounded.
    presence_history_max_rows: int = Field(
        default=500, alias="CONTROL_PLANE_PRESENCE_HISTORY_MAX_ROWS"
    )
    # Age past which a Pi-cached presence bundle should be shown with a
    # staleness hint rather than trusted at face value (NUC-outage drill).
    presence_stale_after_s: float = Field(
        default=1800.0, alias="CONTROL_PLANE_PRESENCE_STALE_AFTER_S"
    )

    @property
    def telegram_chat_id_list(self) -> list[str]:
        return [c.strip() for c in self.telegram_chat_ids.split(",") if c.strip()]

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
