"""The controller's own story reaches /health and /metrics (2026-09 audit, lanes A1/G).

Before this, ``door.controller_health`` — the transport's translation of the ESP32's
1 Hz heartbeat — had no branch in ``handle_contract_event`` and was dropped: fw_version,
fallback_active, cached_profile_id and uptime never reached a log, a metric or an admin
surface. The link's own counters (rx_errors, tx_timeouts, duplicate_rx, queue drops)
existed in ``metrics_text()`` and were never scraped. And ``/health`` said ok with a
configured UART link that had never connected — indistinguishable from a cut cable, and
exactly the "ESP32 offline" case ARCHITECTURE.md §10 wants surfaced as an admin error.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Generator, Mapping
from typing import Any

import pytest
from doorboard_contracts import SessionState
from doorboard_contracts.events import DoorboardEvent, parse_event
from doorboard_contracts.examples import example_event
from doorboard_esp32_link import Esp32TransportStatus, WireMessage
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state  # noqa: E402
from door_api.esp32_link import Esp32LinkSupervisor  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    state.__init__()
    state.startup()
    yield
    state.shutdown()


def _heartbeat(**payload: Any) -> Any:
    as_dict = example_event("door.controller_health").model_dump(mode="json")
    as_dict["payload"] = {**as_dict["payload"], **payload}
    return parse_event(as_dict)


def _metrics() -> str:
    return TestClient(app).get("/metrics").text


# -- telemetry -------------------------------------------------------------------------


def test_before_any_heartbeat_the_gauges_say_so() -> None:
    text = _metrics()
    assert "door_api_esp32_controller_health_age_s -1.0" in text
    assert "door_api_esp32_fallback_active 0" in text
    assert "door_api_esp32_uptime_s 0" in text
    assert 'door_api_esp32_fw_info{fw_version="unknown"} 0' in text


def test_a_heartbeat_claiming_fallback_is_a_gauge_of_one() -> None:
    changed = state.handle_contract_event(
        _heartbeat(fallback_active=True, uptime_s=4242, fw_version="0.3.1")
    )

    assert changed is False, "telemetry never moves the session machine"
    assert state.machine.state == SessionState.IDLE
    text = _metrics()
    assert "door_api_esp32_fallback_active 1" in text
    assert "door_api_esp32_uptime_s 4242" in text
    assert 'door_api_esp32_fw_info{fw_version="0.3.1"} 1' in text
    assert "door_api_esp32_controller_health_events_total 1" in text
    age = next(
        float(line.split()[1])
        for line in text.splitlines()
        if line.startswith("door_api_esp32_controller_health_age_s ")
    )
    assert 0.0 <= age < 5.0


def test_a_later_heartbeat_replaces_the_earlier_one() -> None:
    state.handle_contract_event(_heartbeat(fallback_active=True))
    state.handle_contract_event(_heartbeat(fallback_active=False))
    assert "door_api_esp32_fallback_active 0" in _metrics()
    assert state.esp32_controller_health is not None
    assert state.esp32_controller_health.fallback_active is False


def test_a_fw_version_with_a_quote_cannot_break_the_exposition_format() -> None:
    state.handle_contract_event(_heartbeat(fw_version='0.1"\\x'))
    assert 'door_api_esp32_fw_info{fw_version="0.1\\"\\\\x"} 1' in _metrics()


# -- the link's own counters ----------------------------------------------------------------


class _CountingTransport:
    """A transport that has the concrete transport's metrics_text()."""

    def make_message(self, message_type: str, payload: Mapping[str, object]) -> WireMessage:
        return WireMessage(v=1, seq=1, message_type=message_type, ack=None, payload=payload)

    async def send(self, msg: WireMessage) -> WireMessage:
        return WireMessage(v=1, seq=0, message_type="ack", ack=msg.seq, payload={})

    async def send_event(self, event: DoorboardEvent) -> WireMessage:
        raise NotImplementedError

    def events(self) -> AsyncIterator[DoorboardEvent]:
        raise NotImplementedError

    def status(self) -> Esp32TransportStatus:
        return Esp32TransportStatus(
            connected=True, last_heartbeat_mono_ms=None, rx_errors=2, tx_retries=0
        )

    def metrics_text(self) -> str:
        return "esp32_link_rx_errors_total 2\nesp32_link_tx_timeouts_total 1\n"


def test_the_transports_counters_are_appended_to_metrics() -> None:
    state.esp32_transport = _CountingTransport()  # attached, consumer not needed here
    text = _metrics()
    assert "esp32_link_rx_errors_total 2" in text
    assert "esp32_link_tx_timeouts_total 1" in text
    assert text.endswith("\n")


def test_a_transport_without_counters_contributes_nothing() -> None:
    assert state.esp32_transport_metrics_text() == ""
    assert "esp32_link_rx_errors_total" not in _metrics()


# -- /health knows about the link ---------------------------------------------------------


async def _never_opens() -> Any:
    await asyncio.Event().wait()


def _supervisor() -> Esp32LinkSupervisor:
    return Esp32LinkSupervisor(
        opener=_never_opens,
        attach=lambda _link: None,
        detach=lambda: None,
        target="uart:/dev/ttyACM1@115200",
    )


def test_a_configured_link_that_is_down_degrades_health() -> None:
    supervisor = _supervisor()
    supervisor.open_failures = 3
    state.esp32_link = supervisor

    health = TestClient(app).get("/health").json()

    assert health["status"] == "degraded"
    assert "esp32 link down (open_failures=3)" in health["detail"]


def test_a_connected_link_is_healthy() -> None:
    supervisor = _supervisor()
    supervisor.connected = True
    state.esp32_link = supervisor
    assert TestClient(app).get("/health").json()["status"] == "ok"


def test_no_link_configured_is_not_a_problem() -> None:
    # The default: mock transport, no supervisor. CI and every dev machine.
    assert state.esp32_link is None
    assert TestClient(app).get("/health").json()["status"] == "ok"
