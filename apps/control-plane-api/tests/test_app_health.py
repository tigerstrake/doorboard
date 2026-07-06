from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "control-plane-api"
    assert body["status"] == "ok"


def test_metrics(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "control_plane_api_events_total" in resp.text
