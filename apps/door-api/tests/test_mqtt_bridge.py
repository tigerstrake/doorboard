"""Tests for the optional NUC ambient/presence → /ws MQTT bridge.

These exercise the message-handling seam directly against a fake broadcast, so
no real broker is required. They cover: (a) valid ambient/presence payloads are
parsed and forwarded in the same envelope door-api uses locally; (b) garbage
payloads are swallowed (no exception, nothing broadcast); and (c) the bridge is
inert when DOOR_API_MQTT_URL is empty.
"""

from __future__ import annotations

from typing import Any

from door_api.config import SessionConfig
from door_api.mqtt_bridge import MqttBridge
from doorboard_contracts.examples import example_event


class FakeBroadcast:
    """Records send_delta calls in place of the real DisplayBroadcast."""

    def __init__(self) -> None:
        self.deltas: list[dict[str, Any]] = []

    def send_delta(self, event: dict[str, Any]) -> None:
        self.deltas.append(event)


def _bridge(broadcast: Any) -> MqttBridge:
    return MqttBridge(url="mqtt://nuc.local:1883", broadcast=broadcast)


def test_ambient_aircraft_payload_routes_to_broadcast() -> None:
    broadcast = FakeBroadcast()
    bridge = _bridge(broadcast)
    # The NUC publishes the full DoorboardEvent JSON (event.model_dump_json()).
    payload = example_event("ambient.aircraft_summary").model_dump_json()

    assert bridge.handle_payload(payload) is True

    assert len(broadcast.deltas) == 1
    delta = broadcast.deltas[0]
    assert delta["type"] == "ambient.aircraft_summary"
    assert delta["payload"]["nearby"][0]["callsign"] == "UAL123"
    assert bridge.messages_received == 1
    assert bridge.messages_broadcast == 1
    assert bridge.parse_errors == 0


def test_presence_changed_payload_routes_to_broadcast() -> None:
    broadcast = FakeBroadcast()
    bridge = _bridge(broadcast)
    payload = example_event("status.presence_changed").model_dump_json()

    assert bridge.handle_payload(payload) is True

    assert len(broadcast.deltas) == 1
    delta = broadcast.deltas[0]
    assert delta["type"] == "status.presence_changed"
    assert delta["payload"]["label"] == "available"
    assert bridge.messages_broadcast == 1


def test_bridge_accepts_raw_bytes_payload() -> None:
    # aiomqtt delivers message.payload as bytes; handle_payload must accept it.
    broadcast = FakeBroadcast()
    bridge = _bridge(broadcast)
    payload = example_event("ambient.printer_status").model_dump_json().encode("utf-8")

    assert bridge.handle_payload(payload) is True
    assert broadcast.deltas[0]["type"] == "ambient.printer_status"


def test_garbage_payload_is_swallowed() -> None:
    broadcast = FakeBroadcast()
    bridge = _bridge(broadcast)

    # Neither valid JSON nor a valid event — must not raise, must not broadcast.
    assert bridge.handle_payload(b"not json at all }{") is False
    assert bridge.handle_payload(b'{"type": "totally.unknown", "payload": {}}') is False

    assert broadcast.deltas == []
    assert bridge.messages_received == 2
    assert bridge.messages_broadcast == 0
    assert bridge.parse_errors == 2


def test_bridge_disabled_when_mqtt_url_empty(monkeypatch: Any) -> None:
    # No DOOR_API_MQTT_URL => empty url => bridge must be completely inert.
    for name in (
        "DOOR_API_MQTT_URL",
        "DOOR_API_MQTT_USERNAME",
        "DOOR_API_MQTT_PASSWORD",
        "DOOR_API_MQTT_TOPICS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")

    config = SessionConfig.from_env()

    assert config.mqtt_url == ""
    # Default topic subscription is still available for when a URL is provided.
    assert config.mqtt_topics == ("doorboard/ambient/#", "doorboard/status/#")


def test_mqtt_topics_parsed_from_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_MQTT_URL", "mqtt://nuc.local:1883")
    monkeypatch.setenv("DOOR_API_MQTT_TOPICS", " doorboard/ambient/# , doorboard/status/# , ")

    config = SessionConfig.from_env()

    assert config.mqtt_url == "mqtt://nuc.local:1883"
    assert config.mqtt_topics == ("doorboard/ambient/#", "doorboard/status/#")
