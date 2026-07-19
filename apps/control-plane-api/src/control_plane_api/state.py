"""Process-wide dependencies, built once at startup and stored on `app.state`."""

from __future__ import annotations

from control_plane_api.db import make_engine, make_session_factory
from control_plane_api.mqtt import MqttPublisher, build_publisher
from control_plane_api.notify import NotifyEngine, build_notifier
from control_plane_api.presence import CalendarProvider, MockCalendarProvider
from control_plane_api.settings import Settings
from control_plane_api.telegram import (
    VideoMessageDelivery,
    build_telegram_sender,
    build_video_source,
)


class AppState:
    def __init__(
        self,
        cfg: Settings,
        *,
        mqtt_publisher: MqttPublisher | None = None,
        calendar_provider: CalendarProvider | None = None,
    ) -> None:
        self.settings = cfg
        self.engine = make_engine(cfg.postgres_dsn)
        self.session_factory = make_session_factory(self.engine)
        self.mqtt_publisher = mqtt_publisher or build_publisher(
            url=cfg.mqtt_url, username=cfg.mqtt_username, password=cfg.mqtt_password
        )
        # Owner notifications go to whichever channels are configured — ntfy
        # and/or Telegram (the T-609 bot). The aircraft-proximity rule (T-610)
        # is gated by AIRCRAFT_ALERT_RADIUS_MI (0 = off).
        notifier = build_notifier(
            ntfy_url=cfg.ntfy_url,
            ntfy_topic=cfg.ntfy_topic,
            telegram_bot_token=cfg.telegram_bot_token,
            telegram_chat_ids=cfg.telegram_chat_id_list,
            telegram_api_base_url=cfg.telegram_api_base_url,
        )
        self.notify_engine = NotifyEngine(
            notifier,
            cooldown_s=cfg.notify_cooldown_s,
            sync_stall_alert_s=cfg.sync_stall_alert_s,
            aircraft_alert_radius_km=cfg.aircraft_alert_radius_km,
            aircraft_alert_max_altitude_ft=cfg.aircraft_alert_max_altitude_ft,
            aircraft_alert_cooldown_s=cfg.aircraft_alert_cooldown_s,
            bird_new_species_alert=cfg.bird_new_species_alert,
            bird_known_species=cfg.bird_known_species_set,
            bird_new_species_cooldown_s=cfg.bird_new_species_cooldown_s,
        )
        # Telegram video-message delivery (ADR-0012). Disabled unless a bot
        # token, chat id(s), and door-api media creds are all configured.
        self.video_message_delivery = VideoMessageDelivery(
            sender=build_telegram_sender(
                bot_token=cfg.telegram_bot_token,
                chat_ids=cfg.telegram_chat_id_list,
                api_base_url=cfg.telegram_api_base_url,
            ),
            source=build_video_source(
                door_api_base_url=cfg.door_api_base_url,
                door_api_admin_token=cfg.door_api_admin_token,
            ),
            max_video_bytes=cfg.telegram_max_video_bytes,
        )
        # Real calendar wiring is a later brief (T-504) — `MockCalendarProvider`
        # always returns "no signal", so calendar simply never wins precedence
        # until a real provider is injected.
        self.calendar_provider: CalendarProvider = calendar_provider or MockCalendarProvider()

    def dispose(self) -> None:
        self.engine.dispose()
