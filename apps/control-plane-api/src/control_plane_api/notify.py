"""Owner notifications: missed bell, storage alert, sync stalled.

Channel: **ntfy** (chosen over Home Assistant notify for T-501 — it needs no
HA bridge/entity setup, which is T-503's scope, and is a plain HTTP POST any
phone can subscribe to). `NullNotifier` is used whenever `NTFY_URL`/`NTFY_TOPIC`
are unset (dev/CI default) so notification failures never affect ingest.

Rule evaluation is pure (`evaluate_rules`) and independent of the transport,
so trigger conditions are unit-testable without a network call. `NotifyEngine`
adds a per-rule cooldown (`notification_state` table) so a persistently bad
condition (e.g. `oldest_unsynced_s` staying high) doesn't re-page on every
ingested event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from doorboard_contracts import DoorboardEvent
from sqlalchemy.orm import Session

from control_plane_api.models import NotificationStateRow

logger = logging.getLogger("control_plane_api.notify")


@dataclass(frozen=True, slots=True)
class Notification:
    rule_key: str
    title: str
    message: str
    priority: str = "default"
    # Per-notification cooldown override (seconds); falls back to the engine
    # default when None. Lets a high-frequency rule (aircraft overhead) throttle
    # independently of the slow ones (missed bell, storage).
    cooldown_s: int | None = None


def evaluate_rules(
    event: DoorboardEvent,
    *,
    sync_stall_alert_s: int,
    aircraft_alert_radius_km: float = 0.0,
    aircraft_alert_max_altitude_ft: int = 0,
    aircraft_alert_cooldown_s: int = 600,
    bird_new_species_alert: bool = False,
    bird_known_species: frozenset[str] = frozenset(),
    bird_new_species_cooldown_s: int = 30 * 24 * 3600,
) -> Notification | None:
    if event.type == "session.ended" and event.payload.outcome == "unanswered_timeout":
        return Notification(
            rule_key=f"missed_bell:{event.door_id}",
            title="Missed bell",
            message=f"A visitor rang and left no message (session {event.payload.session_id}).",
        )
    if event.type == "system.storage_alert" and event.payload.severity == "critical":
        return Notification(
            rule_key=f"storage_alert:{event.door_id}:{event.payload.mount}",
            title="Storage critical",
            message=(
                f"{event.payload.host}:{event.payload.mount} has "
                f"{event.payload.free_bytes} bytes free."
            ),
            priority="high",
        )
    if (
        event.type == "media.storage_status"
        and event.payload.oldest_unsynced_s > sync_stall_alert_s
    ):
        hours = event.payload.oldest_unsynced_s / 3600
        return Notification(
            rule_key=f"sync_stalled:{event.door_id}",
            title="Sync falling behind",
            message=f"Oldest unsynced clip is {hours:.1f}h old on door {event.door_id}.",
        )
    # Aircraft overhead: an ambient.aircraft_summary carries every plane in the
    # (wide) page box with its ground distance from the observer centre, so a
    # proximity alert is a pure distance/altitude filter here — the *page* keeps
    # its wide box; only this alert is restricted to the radius. Disabled when
    # radius is 0. Distances are measured from AIRCRAFT_OBSERVER_LAT/LON, so set
    # that to the address you want the radius centred on.
    if event.type == "ambient.aircraft_summary" and aircraft_alert_radius_km > 0:
        overhead = [
            a
            for a in event.payload.nearby
            if a.distance_km <= aircraft_alert_radius_km
            and (
                aircraft_alert_max_altitude_ft <= 0
                or a.altitude_ft <= aircraft_alert_max_altitude_ft
            )
        ]
        if overhead:
            nearest = min(overhead, key=lambda a: a.distance_km)
            extra = f" (+{len(overhead) - 1} more)" if len(overhead) > 1 else ""
            call = nearest.callsign.strip() or "An aircraft"
            return Notification(
                rule_key=f"aircraft_overhead:{event.door_id}",
                title="Plane overhead",
                message=(
                    f"{call} is ~{nearest.distance_km:.1f} km away at "
                    f"{nearest.altitude_ft} ft{extra}."
                ),
                cooldown_s=aircraft_alert_cooldown_s,
            )
    # New bird: alert the first detected species not on the known list (e.g. a
    # bird outside your bundled illustration set). Per-species rule_key + long
    # cooldown => one message the first time each new species shows up, never a
    # repeat for the regulars. Fires only when explicitly enabled.
    if event.type == "ambient.bird_summary" and bird_new_species_alert:
        for species in event.payload.top_species:
            name = species.name.strip()
            if name and name.lower() not in bird_known_species:
                return Notification(
                    rule_key=f"new_bird:{event.door_id}:{name.lower()}",
                    title="New bird detected",
                    message=f"{name} was detected — not on your known-species list.",
                    cooldown_s=bird_new_species_cooldown_s,
                )
    return None


class Notifier(Protocol):
    def notify(self, notification: Notification) -> None: ...


class NullNotifier:
    def notify(self, notification: Notification) -> None:
        logger.info("notify_disabled_skip", extra={"rule_key": notification.rule_key})


class RecordingNotifier:
    """Test double that records what would have been sent."""

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def notify(self, notification: Notification) -> None:
        self.sent.append(notification)


class NtfyNotifier:
    def __init__(self, *, base_url: str, topic: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._topic = topic

    def notify(self, notification: Notification) -> None:
        import httpx

        try:
            httpx.post(
                f"{self._base_url}/{self._topic}",
                content=notification.message.encode("utf-8"),
                headers={"Title": notification.title, "Priority": notification.priority},
                timeout=5.0,
            )
        except Exception:
            logger.warning(
                "ntfy_publish_failed", extra={"rule_key": notification.rule_key}, exc_info=True
            )


class TelegramNotifier:
    """Owner notifications via Telegram, reusing the T-609 bot client (ADR-0012)."""

    def __init__(self, *, bot_token: str, chat_ids: list[str], api_base_url: str) -> None:
        from control_plane_api.telegram import TelegramClient

        self._client = TelegramClient(
            bot_token=bot_token, chat_ids=chat_ids, api_base_url=api_base_url
        )

    def notify(self, notification: Notification) -> None:
        self._client.send_message(text=f"{notification.title}: {notification.message}")


class MultiNotifier:
    """Fans out to every configured channel; one channel failing never blocks the rest."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self._notifiers = notifiers

    def notify(self, notification: Notification) -> None:
        for notifier in self._notifiers:
            try:
                notifier.notify(notification)
            except Exception:
                logger.warning(
                    "notifier_failed", extra={"rule_key": notification.rule_key}, exc_info=True
                )


def build_notifier(
    *,
    ntfy_url: str = "",
    ntfy_topic: str = "",
    telegram_bot_token: str = "",
    telegram_chat_ids: list[str] | None = None,
    telegram_api_base_url: str = "https://api.telegram.org",
) -> Notifier:
    """Route owner notifications to whichever channels are configured (ntfy and/or Telegram)."""
    notifiers: list[Notifier] = []
    if ntfy_url and ntfy_topic:
        notifiers.append(NtfyNotifier(base_url=ntfy_url, topic=ntfy_topic))
    if telegram_bot_token and telegram_chat_ids:
        notifiers.append(
            TelegramNotifier(
                bot_token=telegram_bot_token,
                chat_ids=telegram_chat_ids,
                api_base_url=telegram_api_base_url,
            )
        )
    if not notifiers:
        return NullNotifier()
    if len(notifiers) == 1:
        return notifiers[0]
    return MultiNotifier(notifiers)


class NotifyEngine:
    """Applies the per-rule cooldown on top of a `Notifier`."""

    def __init__(
        self,
        notifier: Notifier,
        *,
        cooldown_s: int,
        sync_stall_alert_s: int,
        aircraft_alert_radius_km: float = 0.0,
        aircraft_alert_max_altitude_ft: int = 0,
        aircraft_alert_cooldown_s: int = 600,
        bird_new_species_alert: bool = False,
        bird_known_species: frozenset[str] = frozenset(),
        bird_new_species_cooldown_s: int = 30 * 24 * 3600,
    ) -> None:
        self._notifier = notifier
        self._cooldown = timedelta(seconds=cooldown_s)
        self._sync_stall_alert_s = sync_stall_alert_s
        self._aircraft_alert_radius_km = aircraft_alert_radius_km
        self._aircraft_alert_max_altitude_ft = aircraft_alert_max_altitude_ft
        self._aircraft_alert_cooldown_s = aircraft_alert_cooldown_s
        self._bird_new_species_alert = bird_new_species_alert
        self._bird_known_species = bird_known_species
        self._bird_new_species_cooldown_s = bird_new_species_cooldown_s

    def on_event(self, session: Session, event: DoorboardEvent, *, now: datetime) -> None:
        notification = evaluate_rules(
            event,
            sync_stall_alert_s=self._sync_stall_alert_s,
            aircraft_alert_radius_km=self._aircraft_alert_radius_km,
            aircraft_alert_max_altitude_ft=self._aircraft_alert_max_altitude_ft,
            aircraft_alert_cooldown_s=self._aircraft_alert_cooldown_s,
            bird_new_species_alert=self._bird_new_species_alert,
            bird_known_species=self._bird_known_species,
            bird_new_species_cooldown_s=self._bird_new_species_cooldown_s,
        )
        if notification is None:
            return
        cooldown = (
            timedelta(seconds=notification.cooldown_s)
            if notification.cooldown_s is not None
            else self._cooldown
        )
        state = session.get(NotificationStateRow, notification.rule_key)
        if state is not None and now - state.last_notified_at < cooldown:
            return
        try:
            self._notifier.notify(notification)
        except Exception:
            logger.warning(
                "notify_failed", extra={"rule_key": notification.rule_key}, exc_info=True
            )
        if state is None:
            session.add(NotificationStateRow(rule_key=notification.rule_key, last_notified_at=now))
        else:
            state.last_notified_at = now
