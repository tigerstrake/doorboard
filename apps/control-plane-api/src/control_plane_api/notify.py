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


def evaluate_rules(event: DoorboardEvent, *, sync_stall_alert_s: int) -> Notification | None:
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


def build_notifier(*, ntfy_url: str, ntfy_topic: str) -> Notifier:
    if not ntfy_url or not ntfy_topic:
        return NullNotifier()
    return NtfyNotifier(base_url=ntfy_url, topic=ntfy_topic)


class NotifyEngine:
    """Applies the per-rule cooldown on top of a `Notifier`."""

    def __init__(self, notifier: Notifier, *, cooldown_s: int, sync_stall_alert_s: int) -> None:
        self._notifier = notifier
        self._cooldown = timedelta(seconds=cooldown_s)
        self._sync_stall_alert_s = sync_stall_alert_s

    def on_event(self, session: Session, event: DoorboardEvent, *, now: datetime) -> None:
        notification = evaluate_rules(event, sync_stall_alert_s=self._sync_stall_alert_s)
        if notification is None:
            return
        state = session.get(NotificationStateRow, notification.rule_key)
        if state is not None and now - state.last_notified_at < self._cooldown:
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
