"""MQTT audit fan-out: topic naming and best-effort behavior."""

from __future__ import annotations

from control_plane_api.mqtt import NullMqttPublisher, RecordingMqttPublisher, topic_for
from doorboard_contracts import parse_event

from .factories import build_event


def test_topic_naming_replaces_dots_with_slashes() -> None:
    assert topic_for("door.button_pressed") == "doorboard/door/button_pressed"
    assert topic_for("social.deletion_requested") == "doorboard/social/deletion_requested"


def test_recording_publisher_captures_topic_and_event() -> None:
    publisher = RecordingMqttPublisher()
    event = parse_event(build_event("system.service_health"))
    publisher.publish_event(event)
    assert len(publisher.published) == 1
    topic, published_event = publisher.published[0]
    assert topic == "doorboard/system/service_health"
    assert published_event is event


def test_null_publisher_never_raises() -> None:
    publisher = NullMqttPublisher()
    event = parse_event(build_event("system.service_health"))
    publisher.publish_event(event)  # must not raise
