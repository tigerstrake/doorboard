"""`/health` probes its dependencies; `/metrics` is cheap and carries the counters.

The previous version of this file asserted `status == "ok"` against a handler
that returned the constant `"ok"`, which proved nothing. These point the engine
at a dead DSN and expect the service to say so.
"""

from __future__ import annotations

import pytest
from control_plane_api import state as state_module
from control_plane_api.app import app
from control_plane_api.metrics import INGEST_EVENTS, MQTT_CONNECTED, metrics
from control_plane_api.mqtt import NullMqttPublisher
from control_plane_api.settings import override_settings
from fastapi.testclient import TestClient

from .conftest import TestSettings
from .factories import build_event

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


def test_health_is_ok_with_a_live_database_and_reports_its_checks(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "control-plane-api"
    assert body["status"] == "ok"
    assert body["detail"] is None
    assert body["checks"]["database"] is True
    # MQTT is not configured in tests: "not configured" is None, never "down".
    assert body["checks"]["mqtt"] is None
    assert body["checks"]["calendar"] is None  # the mock provider has no feeds to be unhealthy


def test_health_is_degraded_when_the_database_is_dead(engine) -> None:
    class DeadDatabaseSettings(TestSettings):
        # Port 1 is never listening: the connect is refused immediately.
        postgres_dsn: str = "postgresql+psycopg://doorboard:doorboard@127.0.0.1:1/doorboard_test"

    override_settings(DeadDatabaseSettings())
    with TestClient(app) as dead:
        resp = dead.get("/health")
    # Still HTTP 200 (the compose healthcheck restart-looping the service would not
    # bring the database back), but honest about it.
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] is False
    assert "database" in body["detail"]


def test_health_reports_a_down_mqtt_link(engine, monkeypatch: pytest.MonkeyPatch) -> None:
    class DownLink(NullMqttPublisher):
        @property
        def connected(self) -> bool | None:
            return False

    monkeypatch.setattr(state_module, "build_publisher", lambda **kwargs: DownLink())
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["checks"]["mqtt"] is False
    assert body["detail"] == "mqtt unavailable"


def test_metrics_uses_the_planner_estimate_and_carries_ingest_counters(
    client: TestClient,
) -> None:
    token = client.post(
        "/admin/tokens", json={"scope": "ingest", "door_id": "primary"}, headers=ADMIN_HEADERS
    ).json()["token"]
    stored_before = metrics.counter(INGEST_EVENTS, status="stored")
    event = build_event("system.service_health")
    client.post(
        "/ingest",
        json={"batch_id": "m", "events": [event, event]},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    # Row count is the O(1) planner estimate (gauge), not a count(*) scan.
    assert "# TYPE control_plane_api_events_total gauge" in text
    assert "reltuples" in text
    assert metrics.counter(INGEST_EVENTS, status="stored") - stored_before == 1
    assert 'control_plane_api_ingest_events_total{status="stored"}' in text
    assert 'control_plane_api_ingest_events_total{status="duplicate"}' in text
    assert "control_plane_api_uptime_s" in text


def test_metrics_exports_the_mqtt_link_gauge_when_configured(
    engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    class UpLink(NullMqttPublisher):
        @property
        def connected(self) -> bool | None:
            return True

    monkeypatch.setattr(state_module, "build_publisher", lambda **kwargs: UpLink())
    with TestClient(app) as client:
        text = client.get("/metrics").text
    assert f"{MQTT_CONNECTED} 1" in text
