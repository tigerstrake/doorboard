"""Process-wide dependencies, built once at startup and stored on `app.state`."""

from __future__ import annotations

from control_plane_api.db import make_engine, make_session_factory
from control_plane_api.mqtt import MqttPublisher, build_publisher
from control_plane_api.notify import NotifyEngine, build_notifier
from control_plane_api.settings import Settings


class AppState:
    def __init__(self, cfg: Settings, *, mqtt_publisher: MqttPublisher | None = None) -> None:
        self.settings = cfg
        self.engine = make_engine(cfg.postgres_dsn)
        self.session_factory = make_session_factory(self.engine)
        self.mqtt_publisher = mqtt_publisher or build_publisher(
            url=cfg.mqtt_url, username=cfg.mqtt_username, password=cfg.mqtt_password
        )
        notifier = build_notifier(ntfy_url=cfg.ntfy_url, ntfy_topic=cfg.ntfy_topic)
        self.notify_engine = NotifyEngine(
            notifier, cooldown_s=cfg.notify_cooldown_s, sync_stall_alert_s=cfg.sync_stall_alert_s
        )

    def dispose(self) -> None:
        self.engine.dispose()
