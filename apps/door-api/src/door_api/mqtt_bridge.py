"""Optional MQTT → WebSocket bridge for NUC-produced ambient/presence events.

The wallboard (apps/door-ui) renders ambient tiles (aircraft, satellites,
printer, birds, food) and a presence tile from ``ambient.*`` / ``status.*``
events it receives over door-api's ``/ws``. Those events are produced on the
NUC control-plane (wallboard-worker + presence engine), which publishes every
ingested :class:`DoorboardEvent` to a Mosquitto broker on
``doorboard/<event.type with '.'→'/'>`` topics with the full event JSON as the
payload (see control-plane-api/mqtt.py ``topic_for`` / ``PahoMqttPublisher``).
door-api has no MQTT client of its own, so without this bridge those events
never reach the wallboard and the tiles stay "unavailable". This module closes
that gap by subscribing to the broker and re-broadcasting matching events onto
the same :class:`~door_api.broadcast.DisplayBroadcast` door-api already uses, so
``/ws`` clients receive them in the *identical* envelope as locally-produced
events.

CRITICAL — isolation from the door interaction path
====================================================
This is a best-effort, default-OFF background task. It is completely inert
unless ``DOOR_API_MQTT_URL`` is set (see :meth:`DoorApiState.start_mqtt_bridge`).
The door's core job (button → session → ESP32 feedback → ``/ws``, plus the HTTP
surface) must be 100% unaffected whether Mosquitto is down, slow, unauthorized,
or sending garbage. Therefore:

* startup never awaits the broker connection — the loop is fire-and-forget;
* the connection loop swallows every exception (logs + capped exponential
  backoff retry) rather than propagating it to the app;
* a malformed payload is logged and skipped, never raised.

Why only ambient/status topics
===============================
door-api already broadcasts its OWN session/vision/social events locally. We
subscribe *only* to ``doorboard/ambient/#`` and ``doorboard/status/#`` (the
config default) so we never re-broadcast — and thereby duplicate on ``/ws`` —
events door-api itself is the source of.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from urllib.parse import urlparse

from doorboard_contracts.events import EVENT_ADAPTER

from door_api.broadcast import DisplayBroadcast

logger = logging.getLogger("door-api.mqtt_bridge")


class MqttBridge:
    """Bridges NUC-published ambient/presence events onto door-api's ``/ws``.

    Message handling (:meth:`handle_payload`) is deliberately decoupled from the
    network loop (:meth:`run`) so it can be unit-tested against a fake broadcast
    without a real broker.
    """

    def __init__(
        self,
        *,
        url: str,
        broadcast: DisplayBroadcast,
        topics: Sequence[str] = ("doorboard/ambient/#", "doorboard/status/#"),
        username: str = "",
        password: str = "",
        reconnect_base_s: float = 1.0,
        reconnect_max_s: float = 60.0,
    ) -> None:
        self.url = url
        self.broadcast = broadcast
        self.topics = tuple(topics)
        self.username = username
        self.password = password
        self.reconnect_base_s = reconnect_base_s
        self.reconnect_max_s = reconnect_max_s

        # Best-effort observability counters (surfaced via /metrics).
        self.messages_received = 0
        self.messages_broadcast = 0
        self.parse_errors = 0

    def handle_payload(self, payload: bytes | bytearray | str) -> bool:
        """Parse one MQTT payload into a DoorboardEvent and broadcast it.

        Returns ``True`` when the event was broadcast, ``False`` when the
        payload was dropped. Never raises for bad input: a malformed/garbage
        payload is logged at WARNING and swallowed so the bridge keeps running.
        """
        self.messages_received += 1
        try:
            event = EVENT_ADAPTER.validate_json(payload)
        except Exception:
            self.parse_errors += 1
            logger.warning("mqtt_bridge_parse_failed", exc_info=True)
            return False

        # Same envelope door-api uses for locally-produced contract events
        # (cf. the vision.* handlers in app.py): send_delta wraps the event dict
        # as {"type": "delta", "event": {...}} for /ws clients.
        self.broadcast.send_delta(event.model_dump(mode="json"))
        self.messages_broadcast += 1
        logger.debug("mqtt_bridge_broadcast", extra={"type": event.type})
        return True

    async def run(self) -> None:
        """Connect, subscribe, and forward messages forever with reconnect.

        Any failure (broker down/slow/unauthorized, network blip) is logged and
        retried with capped exponential backoff. Only ``CancelledError`` (task
        shutdown) escapes; nothing here is ever propagated to the app.
        """
        # Imported lazily so the module stays importable (and the bridge stays
        # inert / testable) even where aiomqtt isn't installed.
        import aiomqtt

        parsed = urlparse(self.url)
        hostname = parsed.hostname or "localhost"
        port = parsed.port or 1883
        client = aiomqtt.Client(
            hostname=hostname,
            port=port,
            username=self.username or None,
            password=self.password or None,
        )

        backoff = self.reconnect_base_s
        while True:
            try:
                async with client:
                    for topic in self.topics:
                        await client.subscribe(topic)
                    logger.info(
                        "mqtt_bridge_connected",
                        extra={"host": hostname, "port": port, "topics": list(self.topics)},
                    )
                    backoff = self.reconnect_base_s  # reset after a clean connect
                    async for message in client.messages:
                        self.handle_payload(message.payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "mqtt_bridge_connection_error_retrying",
                    extra={"retry_in_s": backoff},
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(self.reconnect_max_s, backoff * 2)
