from __future__ import annotations

from unittest.mock import MagicMock

from control_plane_api.app import app
from fastapi.testclient import TestClient

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


def test_alertmanager_webhook_success(client: TestClient, monkeypatch) -> None:
    # Get the state and mock the notifier
    state = app.state.app_state
    mock_notifier = MagicMock()
    monkeypatch.setattr(state.notify_engine, "_notifier", mock_notifier)

    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "StorageLow",
                    "severity": "critical",
                },
                "annotations": {
                    "summary": "Storage low on mount /mnt/ssd",
                    "description": "Remaining free space: 2GB",
                },
            }
        ],
    }

    resp = client.post("/admin/alerts", json=payload, headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"status": "processed"}

    # Assert notifier was called
    assert mock_notifier.notify.call_count == 1
    notification = mock_notifier.notify.call_args[0][0]
    assert notification.rule_key == "alertmanager:StorageLow"
    assert notification.title == "[FIRING] StorageLow"
    assert "Storage low on mount /mnt/ssd" in notification.message
    assert "Remaining free space: 2GB" in notification.message
    assert notification.priority == "high"


def test_alertmanager_webhook_unauthorized(client: TestClient) -> None:
    payload = {"status": "firing", "alerts": []}
    resp = client.post("/admin/alerts", json=payload)
    assert resp.status_code == 401
