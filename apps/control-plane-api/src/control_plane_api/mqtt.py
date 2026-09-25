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

Two more things the callbacks carry (found by the 2026-09 audit):

- paho's QoS-0 `publish()` while the socket is not up returns
  `MQTT_ERR_NO_CONN` and drops the message. The return code is checked and
  counted (`control_plane_api_mqtt_publish_failures_total`) instead of thrown
  away, so "published" in the log means the broker actually took it.
- `on_connected` fires from `_on_connect` on every (re)connect. The presence
  republish runs from it, so it happens after the socket exists (not in the
  same lifespan turn as `connect_async`, which raced and lost 20/20 times) and
  again after every broker restart — mosquitto autosaves retained state only
  every 30 min, so a crash could otherwise lose the last half hour of it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from doorboard_contracts import DoorboardEvent
from doorboard_contracts.events import StatusPresenceChangedEvent

from control_plane_api.metrics import MQTT_CONNECTED, MQTT_PUBLISH_FAILURES, metrics

logger = logging.getLogger("control_plane_api.mqtt")

# paho's `MQTT_ERR_SUCCESS`, spelled out so the fake client in tests and the
# real one agree without importing paho at module import time.
_RC_SUCCESS = 0

# When publishing while the broker link is down, warn at most this often so a
# dead link is visible in logs without flooding them per-event.
_DISCONNECTED_WARN_INTERVAL_S = 30.0


def topic_for(event_type: str) -> str:
    return "doorboard/" + event_type.replace(".", "/")


PRESENCE_TOPIC = topic_for("status.presence_changed")


def presence_topic_for(subject_id: str) -> str:
    """The retained per-subject presence topic (ADR-0048 §1).

    One retained topic per subject, so each subject's last label survives a broker
    or consumer restart. On the single shared topic only the last-published subject
    survived, which meant that after every NUC restart the owner's tile stayed
    "unavailable" until their presence next changed — hours, on a quiet evening.
    """
    return f"{PRESENCE_TOPIC}/{subject_id}"


def topics_for(event: DoorboardEvent) -> tuple[str, ...]:
    """Every topic an event is published to; the first is the primary one.

    `status.presence_changed` goes to its per-subject topic AND, for one release,
    to the un-suffixed legacy topic so a door-api built before ADR-0048 keeps
    receiving it. Everything else is published once.
    """
    if isinstance(event, StatusPresenceChangedEvent):
        return (presence_topic_for(event.payload.subject_id), PRESENCE_TOPIC)
    return (topic_for(event.type),)


class MqttPublisher(Protocol):
    def publish_event(self, event: DoorboardEvent) -> None: ...

    @property
    def connected(self) -> bool | None:
        """True/False for a real link; None when MQTT is not configured at all."""
        ...


class NullMqttPublisher:
    """No-op publisher used when MQTT_URL is unset (dev/CI/mock mode)."""

    def publish_event(self, event: DoorboardEvent) -> None:
        logger.debug("mqtt_disabled_skip_publish", extra={"type": event.type})

    @property
    def connected(self) -> bool | None:
        return None


class RecordingMqttPublisher:
    """Test double that records what would have been published."""

    def __init__(self) -> None:
        # One entry per event, keyed by its primary topic; `topics` lists every
        # topic each publish fanned out to (the legacy presence topic included).
        self.published: list[tuple[str, DoorboardEvent]] = []
        self.topics: list[str] = []

    def publish_event(self, event: DoorboardEvent) -> None:
        topics = topics_for(event)
        self.published.append((topics[0], event))
        self.topics.extend(topics)

    @property
    def connected(self) -> bool | None:
        return True


class PahoMqttPublisher:
    """Production publisher backed by `paho-mqtt`.

    Non-blocking and best-effort. `connect_async()` queues the connection so
    construction never blocks (or fails) on a down broker; `loop_start()`'s
    background thread performs the connect and every subsequent reconnect,
    bounded to 1s..30s by `reconnect_delay_set`. Connection state is tracked
    from the callbacks so `publish_event` can flag a dead link without ever
    raising.
    """

    def __init__(
        self,
        *,
        url: str,
        username: str = "",
        password: str = "",
        on_connected: Callable[[PahoMqttPublisher], None] | None = None,
    ) -> None:
        from urllib.parse import urlparse

        import paho.mqtt.client as mqtt
        from paho.mqtt.enums import CallbackAPIVersion

        parsed = urlparse(url)
        self._connected = False
        self._last_disconnected_warn = 0.0
        self._last_dropped_warn = 0.0
        # Runs on paho's network thread after every successful (re)connect, with
        # this publisher as its argument so the caller never races the
        # constructor's own attribute assignment.
        self._on_connected = on_connected
        metrics.set_gauge(MQTT_CONNECTED, 0)
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
            metrics.set_gauge(MQTT_CONNECTED, 0)
            logger.warning("mqtt_connect_refused", extra={"reason": str(reason_code)})
            return
        self._connected = True
        metrics.set_gauge(MQTT_CONNECTED, 1)
        logger.info("mqtt_connected", extra={"reason": str(reason_code)})
        if self._on_connected is not None:
            try:
                self._on_connected(self)
            except Exception:
                logger.warning("mqtt_on_connected_hook_failed", exc_info=True)

    def _on_disconnect(
        self,
        client: Any,
        userdata: Any,
        disconnect_flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        self._connected = False
        metrics.set_gauge(MQTT_CONNECTED, 0)
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

    @property
    def connected(self) -> bool | None:
        return self._is_connected()

    def _warn_disconnected(self, event: DoorboardEvent) -> None:
        now = time.monotonic()
        if now - self._last_disconnected_warn >= _DISCONNECTED_WARN_INTERVAL_S:
            self._last_disconnected_warn = now
            logger.warning("mqtt_publish_while_disconnected", extra={"type": event.type})

    def _record_dropped(self, event: DoorboardEvent, topic: str, rc: int) -> None:
        # Counted every time; logged at most once per interval so a long outage
        # is one warning line per 30 s, not one per event.
        metrics.inc(MQTT_PUBLISH_FAILURES)
        now = time.monotonic()
        if now - self._last_dropped_warn >= _DISCONNECTED_WARN_INTERVAL_S:
            self._last_dropped_warn = now
            logger.warning(
                "mqtt_publish_dropped", extra={"type": event.type, "topic": topic, "rc": rc}
            )

    def publish_event(self, event: DoorboardEvent) -> None:
        try:
            if not self._is_connected():
                # Link is down. QoS-0 while disconnected is dropped by paho;
                # surface it (throttled) but still hand it over — best-effort,
                # never raise.
                self._warn_disconnected(event)
            # Retain ambient/status topics, so the broker holds the last value per topic and
            # a subscriber that connects later is told it immediately.
            #
            # ADR-0027 gave door-api its own in-memory last-value cache, which covers a
            # wallboard *reload*. It cannot cover a door-api *restart* — the cache dies with
            # the process, and the dining recommendation republishes only once a day, so
            # restarting door-api blanks that tile for up to 24 hours. Retention closes that:
            # the bridge resubscribes on reconnect and the broker replays.
            #
            # Deliberately not retained for session/vision/social events: those are
            # transitions, not state. A retained "the bell is ringing" would be delivered to
            # every future subscriber as though it had just happened.
            retain = event.type.startswith(("ambient.", "status."))
            payload = event.model_dump_json()
            for topic in topics_for(event):
                info = self._client.publish(topic, payload, retain=retain)
                # paho returns MQTTMessageInfo; rc != 0 means it did NOT queue the
                # message (NO_CONN while the socket is down, queue full, ...).
                rc = int(getattr(info, "rc", _RC_SUCCESS) or _RC_SUCCESS)
                if rc != _RC_SUCCESS:
                    self._record_dropped(event, topic, rc)
        except Exception:
            logger.warning("mqtt_publish_failed", extra={"type": event.type}, exc_info=True)

    def close(self) -> None:
        # Send DISCONNECT while the network loop is still running so the broker
        # sees a graceful close (and any in-flight publish is flushed); only then
        # stop the loop. The reverse order drops the link without a DISCONNECT.
        self._client.disconnect()
        self._client.loop_stop()


def build_publisher(
    *,
    url: str,
    username: str = "",
    password: str = "",
    on_connected: Callable[[PahoMqttPublisher], None] | None = None,
) -> MqttPublisher:
    if not url:
        return NullMqttPublisher()
    try:
        # connect_async never blocks/raises on a down broker, so a Paho
        # publisher is returned even if the broker is briefly unavailable at
        # boot; it connects when the broker is reachable. The Null fallback is
        # reserved for a genuinely fatal client-construction error.
        return PahoMqttPublisher(
            url=url, username=username, password=password, on_connected=on_connected
        )
    except Exception:
        logger.warning("mqtt_client_construction_failed_falling_back_to_null", exc_info=True)
        return NullMqttPublisher()
