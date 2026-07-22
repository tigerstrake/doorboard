"""MQTT audit fan-out: topic naming, best-effort behavior, and reconnect/observability.

The reconnect + observability tests use a fake paho client (installed via the
`fake_clients` fixture) so they never touch a real broker.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from control_plane_api.mqtt import (
    NullMqttPublisher,
    PahoMqttPublisher,
    RecordingMqttPublisher,
    build_publisher,
    topic_for,
)
from doorboard_contracts import parse_event

from .factories import build_event

_MQTT_LOGGER = "control_plane_api.mqtt"


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


# ---------------------------------------------------------------------------
# Fake paho client (no real broker touched)
# ---------------------------------------------------------------------------


class FakeReasonCode:
    """Stand-in for paho v2 `ReasonCode` (exposes `is_failure` and `str()`)."""

    def __init__(self, *, is_failure: bool, text: str) -> None:
        self.is_failure = is_failure
        self._text = text

    def __str__(self) -> str:
        return self._text


class FakeClient:
    """Records what `PahoMqttPublisher` does to the paho client, no networking."""

    def __init__(self) -> None:
        self.on_connect: Any = None
        self.on_disconnect: Any = None
        self.connected: bool = False
        self.published: list[tuple[str, Any]] = []
        self.reconnect_delay: tuple[int, int] | None = None
        self.connect_async_args: tuple[str, int] | None = None
        self.loop_started: bool = False
        self.disconnected: bool = False
        self.publish_raises: bool = False
        self.credentials: tuple[str, str | None] | None = None

    def username_pw_set(self, username: str, password: str | None = None) -> None:
        self.credentials = (username, password)

    def reconnect_delay_set(self, min_delay: int = 1, max_delay: int = 120) -> None:
        self.reconnect_delay = (min_delay, max_delay)

    def connect_async(self, host: str, port: int = 1883, *args: Any, **kwargs: Any) -> None:
        self.connect_async_args = (host, port)

    def loop_start(self) -> None:
        self.loop_started = True

    def loop_stop(self, *args: Any, **kwargs: Any) -> None:
        self.loop_started = False

    def is_connected(self) -> bool:
        return self.connected

    def publish(self, topic: str, payload: Any = None, *args: Any, **kwargs: Any) -> Any:
        if self.publish_raises:
            raise RuntimeError("boom")
        self.published.append((topic, payload))
        return None

    def disconnect(self, *args: Any, **kwargs: Any) -> None:
        self.disconnected = True
        self.connected = False


@pytest.fixture
def fake_clients(monkeypatch: pytest.MonkeyPatch) -> list[FakeClient]:
    """Replace `paho.mqtt.client.Client` with a fake; return created instances."""
    import paho.mqtt.client as paho_client

    created: list[FakeClient] = []

    def factory(*args: Any, **kwargs: Any) -> FakeClient:
        client = FakeClient()
        created.append(client)
        return client

    monkeypatch.setattr(paho_client, "Client", factory)
    return created


# ---------------------------------------------------------------------------
# Reconnect wiring
# ---------------------------------------------------------------------------


def test_paho_publisher_uses_nonblocking_connect_with_bounded_reconnect(
    fake_clients: list[FakeClient],
) -> None:
    publisher = PahoMqttPublisher(url="mqtt://broker.local:1884", username="u", password="p")

    assert len(fake_clients) == 1
    client = fake_clients[0]
    # Bounded auto-reconnect so a broker restart recovers within seconds.
    assert client.reconnect_delay == (1, 30)
    # Non-blocking connect + background loop so startup never blocks on broker.
    assert client.connect_async_args == ("broker.local", 1884)
    assert client.loop_started is True
    assert client.credentials == ("u", "p")
    # Callbacks are wired so link state is observable. (Bound methods compare
    # equal by `==`, not `is` — each attribute access makes a fresh object.)
    assert client.on_connect == publisher._on_connect
    assert client.on_disconnect == publisher._on_disconnect


# ---------------------------------------------------------------------------
# Observability: on_connect / on_disconnect
# ---------------------------------------------------------------------------


def test_on_connect_success_logs_info(
    fake_clients: list[FakeClient], caplog: pytest.LogCaptureFixture
) -> None:
    publisher = PahoMqttPublisher(url="mqtt://localhost")
    flags: dict[str, Any] = {}
    with caplog.at_level(logging.INFO, logger=_MQTT_LOGGER):
        publisher._on_connect(
            fake_clients[0], None, flags, FakeReasonCode(is_failure=False, text="Success")
        )

    records = [r for r in caplog.records if r.getMessage() == "mqtt_connected"]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    assert publisher._connected is True


def test_on_connect_failure_logs_warning(
    fake_clients: list[FakeClient], caplog: pytest.LogCaptureFixture
) -> None:
    publisher = PahoMqttPublisher(url="mqtt://localhost")
    flags: dict[str, Any] = {}
    with caplog.at_level(logging.INFO, logger=_MQTT_LOGGER):
        publisher._on_connect(
            fake_clients[0], None, flags, FakeReasonCode(is_failure=True, text="Not authorized")
        )

    records = [r for r in caplog.records if r.getMessage() == "mqtt_connect_refused"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert publisher._connected is False


def test_on_disconnect_logs_warning(
    fake_clients: list[FakeClient], caplog: pytest.LogCaptureFixture
) -> None:
    publisher = PahoMqttPublisher(url="mqtt://localhost")
    publisher._connected = True
    flags: dict[str, Any] = {}
    with caplog.at_level(logging.INFO, logger=_MQTT_LOGGER):
        publisher._on_disconnect(
            fake_clients[0], None, flags, FakeReasonCode(is_failure=True, text="Connection lost")
        )

    records = [r for r in caplog.records if r.getMessage() == "mqtt_disconnected_will_reconnect"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert publisher._connected is False


# ---------------------------------------------------------------------------
# Publish while disconnected: throttled warning, never raises, still best-effort
# ---------------------------------------------------------------------------


def test_publish_while_disconnected_warns_throttled_and_never_raises(
    fake_clients: list[FakeClient], caplog: pytest.LogCaptureFixture
) -> None:
    publisher = PahoMqttPublisher(url="mqtt://localhost")
    client = fake_clients[0]
    client.connected = False
    event = parse_event(build_event("system.service_health"))

    with caplog.at_level(logging.WARNING, logger=_MQTT_LOGGER):
        publisher.publish_event(event)  # must not raise
        publisher.publish_event(event)  # must not raise

    warnings = [r for r in caplog.records if r.getMessage() == "mqtt_publish_while_disconnected"]
    # Throttled: the second call within the window stays silent.
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    # Best-effort: events are still handed to paho even while disconnected.
    assert len(client.published) == 2


def test_publish_while_connected_does_not_warn(
    fake_clients: list[FakeClient], caplog: pytest.LogCaptureFixture
) -> None:
    publisher = PahoMqttPublisher(url="mqtt://localhost")
    client = fake_clients[0]
    client.connected = True
    event = parse_event(build_event("system.service_health"))

    with caplog.at_level(logging.WARNING, logger=_MQTT_LOGGER):
        publisher.publish_event(event)

    assert not [r for r in caplog.records if r.getMessage() == "mqtt_publish_while_disconnected"]
    assert client.published == [("doorboard/system/service_health", event.model_dump_json())]


def test_publish_swallows_client_errors(
    fake_clients: list[FakeClient], caplog: pytest.LogCaptureFixture
) -> None:
    publisher = PahoMqttPublisher(url="mqtt://localhost")
    client = fake_clients[0]
    client.connected = True
    client.publish_raises = True
    event = parse_event(build_event("system.service_health"))

    with caplog.at_level(logging.WARNING, logger=_MQTT_LOGGER):
        publisher.publish_event(event)  # must not raise

    assert any(r.getMessage() == "mqtt_publish_failed" for r in caplog.records)


def test_close_stops_loop_and_disconnects(fake_clients: list[FakeClient]) -> None:
    publisher = PahoMqttPublisher(url="mqtt://localhost")
    client = fake_clients[0]
    publisher.close()
    assert client.loop_started is False
    assert client.disconnected is True


# ---------------------------------------------------------------------------
# build_publisher: Null only when MQTT_URL is empty
# ---------------------------------------------------------------------------


def test_build_publisher_returns_null_only_when_url_empty(
    fake_clients: list[FakeClient],
) -> None:
    assert isinstance(build_publisher(url=""), NullMqttPublisher)


def test_build_publisher_returns_paho_when_url_set_even_if_broker_down(
    fake_clients: list[FakeClient],
) -> None:
    publisher = build_publisher(url="mqtt://localhost:1883")
    assert isinstance(publisher, PahoMqttPublisher)
    # connect_async is non-blocking, so a broker that is down at boot does not
    # force the Null fallback — it connects once the broker is reachable.
    assert fake_clients[0].connect_async_args == ("localhost", 1883)


def test_build_publisher_falls_back_to_null_on_fatal_construction_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import paho.mqtt.client as paho_client

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("cannot construct client")

    monkeypatch.setattr(paho_client, "Client", boom)

    with caplog.at_level(logging.WARNING, logger=_MQTT_LOGGER):
        publisher = build_publisher(url="mqtt://localhost:1883")

    assert isinstance(publisher, NullMqttPublisher)
    assert any("falling_back_to_null" in r.getMessage() for r in caplog.records)
