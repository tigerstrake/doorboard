"""MQTT audit fan-out: publish ingested events to `doorboard/#` for HA/monitoring.

Best-effort, always. A broker outage must never fail ingestion (ARCHITECTURE.md
§10 — Mosquitto is control-plane-internal, not the critical path). Callers
therefore always use `publish_event`, never the raw client, and it swallows
every exception after logging.
"""

from __future__ import annotations

import logging
from typing import Protocol

from doorboard_contracts import DoorboardEvent

logger = logging.getLogger("control_plane_api.mqtt")


def topic_for(event_type: str) -> str:
    return "doorboard/" + event_type.replace(".", "/")


class MqttPublisher(Protocol):
    def publish_event(self, event: DoorboardEvent) -> None: ...


class NullMqttPublisher:
    """No-op publisher used when MQTT_URL is unset (dev/CI/mock mode)."""

    def publish_event(self, event: DoorboardEvent) -> None:
        logger.debug("mqtt_disabled_skip_publish", extra={"type": event.type})


class RecordingMqttPublisher:
    """Test double that records what would have been published."""

    def __init__(self) -> None:
        self.published: list[tuple[str, DoorboardEvent]] = []

    def publish_event(self, event: DoorboardEvent) -> None:
        self.published.append((topic_for(event.type), event))


class PahoMqttPublisher:
    """Production publisher backed by `paho-mqtt`."""

    def __init__(self, *, url: str, username: str = "", password: str = "") -> None:
        from urllib.parse import urlparse

        import paho.mqtt.client as mqtt
        from paho.mqtt.enums import CallbackAPIVersion

        parsed = urlparse(url)
        self._client = mqtt.Client(CallbackAPIVersion.VERSION2)
        if username:
            self._client.username_pw_set(username, password or None)
        self._client.connect(parsed.hostname or "localhost", parsed.port or 1883)
        self._client.loop_start()

    def publish_event(self, event: DoorboardEvent) -> None:
        try:
            self._client.publish(topic_for(event.type), event.model_dump_json())
        except Exception:
            logger.warning("mqtt_publish_failed", extra={"type": event.type}, exc_info=True)

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


def build_publisher(*, url: str, username: str = "", password: str = "") -> MqttPublisher:
    if not url:
        return NullMqttPublisher()
    try:
        return PahoMqttPublisher(url=url, username=username, password=password)
    except Exception:
        logger.warning("mqtt_connect_failed_falling_back_to_null", exc_info=True)
        return NullMqttPublisher()
