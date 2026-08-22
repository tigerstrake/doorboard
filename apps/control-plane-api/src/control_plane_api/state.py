"""Process-wide dependencies, built once at startup and stored on `app.state`."""

from __future__ import annotations

import logging
from datetime import datetime

from doorboard_contracts import PresenceLabel

from control_plane_api.calendar_ics import IcsCalendarProvider, parse_subject_urls
from control_plane_api.db import make_engine, make_session_factory
from control_plane_api.mqtt import MqttPublisher, build_publisher
from control_plane_api.notify import NotifyEngine, build_notifier
from control_plane_api.presence import (
    CalendarProvider,
    MockCalendarProvider,
    NightlyScheduleProvider,
    ScheduleProvider,
    parse_window,
)
from control_plane_api.settings import Settings
from control_plane_api.telegram import (
    RingPhotoDelivery,
    VideoMessageDelivery,
    build_telegram_sender,
    build_thumbnail_source,
    build_video_source,
)

logger = logging.getLogger("control_plane_api.state")


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
            doorbell_notify_enabled=cfg.doorbell_notify_enabled,
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
            # Per-recipient routing (ADR-0014): a saved message carrying chosen
            # recipient keys goes only to those residents' chats; an empty map
            # or a message with no recipients falls back to broadcasting to all
            # TELEGRAM_CHAT_IDS.
            recipient_map=cfg.video_message_recipient_map,
        )
        # A picture of whoever rang (ADR-0022). Same credentials, separate delivery: the
        # ring text goes out on RINGING via the notify engine, this follows when
        # door-media has cut a thumbnail. Disabled by the same "unless configured" rule,
        # and gated on RING_PHOTO_ENABLED so the owner can have the text without the
        # picture.
        self.ring_photo_delivery = RingPhotoDelivery(
            sender=(
                build_telegram_sender(
                    bot_token=cfg.telegram_bot_token,
                    chat_ids=cfg.telegram_chat_id_list,
                    api_base_url=cfg.telegram_api_base_url,
                )
                if cfg.ring_photo_enabled
                else None
            ),
            source=build_thumbnail_source(
                door_api_base_url=cfg.door_api_base_url,
                door_api_admin_token=cfg.door_api_admin_token,
            ),
        )
        # An injected provider always wins (tests). Otherwise: a real .ics provider
        # when feeds are configured, else the mock, which always answers "no signal"
        # so the calendar source simply never wins precedence (ADR-0036).
        if calendar_provider is not None:
            self.calendar_provider: CalendarProvider = calendar_provider
        else:
            subject_urls = parse_subject_urls(cfg.presence_calendar_ics_urls)
            if subject_urls:
                self.calendar_provider = IcsCalendarProvider(
                    subject_urls,
                    refresh_s=cfg.presence_calendar_refresh_s,
                    timeout_s=cfg.presence_calendar_timeout_s,
                )
                logger.info(
                    "calendar_source_enabled",
                    extra={"subjects": sorted(subject_urls)},  # names only, never the URLs
                )
            else:
                self.calendar_provider = MockCalendarProvider()

        # Nightly schedule (ADR-0037). None when unconfigured, which the engine
        # reads as "no schedule source", so presence resolves exactly as before.
        self.schedule_provider: ScheduleProvider | None = None
        window = parse_window(cfg.presence_schedule_window)
        if window is not None:
            subjects = [s.strip() for s in cfg.presence_schedule_subjects.split(",") if s.strip()]
            self.schedule_provider = NightlyScheduleProvider(
                window,
                label=PresenceLabel(cfg.presence_schedule_label),
                subject_ids=subjects or None,
            )
            # The resolved zone is logged because the window is expressed in LOCAL
            # time and the container's zone comes from TZ. Without TZ passed in,
            # this silently runs in UTC — a 23:00 window starting at 16:00 local.
            # Found exactly that way on 2026-08-22.
            local_now = datetime.now().astimezone()
            logger.info(
                "presence_schedule_enabled",
                extra={
                    "window": cfg.presence_schedule_window,
                    "label": cfg.presence_schedule_label,
                    "subjects": subjects or ["*"],
                    "timezone": str(local_now.tzinfo),
                    "utc_offset": local_now.strftime("%z"),
                },
            )

    def dispose(self) -> None:
        self.engine.dispose()
