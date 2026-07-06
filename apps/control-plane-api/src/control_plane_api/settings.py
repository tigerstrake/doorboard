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

    # ── admin auth (stopgap — see packages/auth README) ──────────────────
    admin_token: str = Field(default="", alias="CONTROL_PLANE_ADMIN_TOKEN")

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
