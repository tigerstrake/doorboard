"""MQTT audit fan-out: publish ingested events to `doorboard/#` for HA/monitoring.

Best-effort, always. A broker outage must never fail ingestion (ARCHITECTURE.md
§10 — Mosquitto is control-plane-internal, not the critical path). Callers
therefore always use `publish_event`, never the raw client, and it swallows
every exception after logging.

The Paho publisher is long-lived and must survive broker restarts. It uses
`connect_async()` + `loop_start()` so startup never blocks on the broker, and
paho's network loop owns (re)connection with a bounded backoff
(`reconnect_delay_set`). `on_connect`/`on_disconnect` make the link state
observable: an earlier production incident had the publisher go
dead-but-silent after a broker restart — `publish()` kept returning success
while nothing reached the broker and nothing was logged. The callbacks plus a
throttled "publishing while disconnected" warning close that visibility gap.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from doorboard_contracts import DoorboardEvent

logger = logging.getLogger("control_plane_api.mqtt")

# When publishing while the broker link is down, warn at most this often so a
# dead link is visible in logs without flooding them per-event.
_DISCONNECTED_WARN_INTERVAL_S = 30.0


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
    """Production publisher backed by `paho-mqtt`.

    Non-blocking and best-effort. `connect_async()` queues the connection so
    construction never blocks (or fails) on a down broker; `loop_start()`'s
    background thread performs the connect and every subsequent reconnect,
    bounded to 1s..30s by `reconnect_delay_set`. Connection state is tracked
    from the callbacks so `publish_event` can flag a dead link without ever
    raising.
    """

    def __init__(self, *, url: str, username: str = "", password: str = "") -> None:
        from urllib.parse import urlparse

        import paho.mqtt.client as mqtt
        from paho.mqtt.enums import CallbackAPIVersion

        parsed = urlparse(url)
        self._connected = False
        self._last_disconnected_warn = 0.0
        self._client = mqtt.Client(CallbackAPIVersion.VERSION2)
        if username:
            self._client.username_pw_set(username, password or None)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        # Bounded auto-reconnect so a broker restart is recovered from within
        # seconds. paho's loop reconnects after any unexpected drop.
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        host = parsed.hostname or "localhost"
        port = parsed.port or 1883
        # Non-blocking: queue the connection; the loop thread performs it (and
        # all future reconnects). Never blocks startup even if the broker is
        # unreachable right now.
        self._client.connect_async(host, port)
        self._client.loop_start()

    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        if getattr(reason_code, "is_failure", False):
            self._connected = False
            logger.warning("mqtt_connect_refused", extra={"reason": str(reason_code)})
        else:
            self._connected = True
            logger.info("mqtt_connected", extra={"reason": str(reason_code)})

    def _on_disconnect(
        self,
        client: Any,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        self._connected = False
        logger.warning(
            "mqtt_disconnected_will_reconnect",
            extra={"reason": str(reason_code)},
        )

    def _is_connected(self) -> bool:
        # Prefer paho's own view of the socket; fall back to our callback flag
        # if the client can't answer for any reason.
        try:
            return bool(self._client.is_connected())
        except Exception:
            return self._connected

    def _warn_disconnected(self, event: DoorboardEvent) -> None:
        now = time.monotonic()
        if now - self._last_disconnected_warn >= _DISCONNECTED_WARN_INTERVAL_S:
            self._last_disconnected_warn = now
            logger.warning("mqtt_publish_while_disconnected", extra={"type": event.type})

    def publish_event(self, event: DoorboardEvent) -> None:
        try:
            if not self._is_connected():
                # Link is down. QoS-0 while disconnected is dropped by paho;
                # surface it (throttled) but still hand it over — best-effort,
                # never raise.
                self._warn_disconnected(event)
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
        # connect_async never blocks/raises on a down broker, so a Paho
        # publisher is returned even if the broker is briefly unavailable at
        # boot; it connects when the broker is reachable. The Null fallback is
        # reserved for a genuinely fatal client-construction error.
        return PahoMqttPublisher(url=url, username=username, password=password)
    except Exception:
        logger.warning("mqtt_client_construction_failed_falling_back_to_null", exc_info=True)
        return NullMqttPublisher()
