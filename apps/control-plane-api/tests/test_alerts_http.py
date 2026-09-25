"""`POST /admin/alerts`: the Alertmanager webhook reaches the owner's channels.

infra/monitoring/alertmanager.yml already posts here with the admin bearer token
(from a file). Before this route existed those posts 404'd, so a dead broker or a
full NUC disk never reached the phone that gets the missed-bell pings.
"""

from __future__ import annotations

import time

import pytest
from control_plane_api import state as state_module
from control_plane_api.app import app
from control_plane_api.metrics import ALERTS_RECEIVED, metrics
from control_plane_api.notify import Notification, RecordingNotifier
from fastapi.testclient import TestClient

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}

# A faithful Alertmanager v4 payload, including the top-level fields we ignore.
V4_PAYLOAD = {
    "version": "4",
    "groupKey": '{}:{alertname="MosquittoDown"}',
    "truncatedAlerts": 0,
    "status": "firing",
    "receiver": "control-plane-api-webhook",
    "groupLabels": {"alertname": "MosquittoDown"},
    "commonLabels": {"alertname": "MosquittoDown", "severity": "critical"},
    "commonAnnotations": {},
    "externalURL": "http://alertmanager:9093",
    "alerts": [
        {
            "status": "firing",
            "labels": {"alertname": "MosquittoDown", "severity": "critical", "job": "mosquitto"},
            "annotations": {"summary": "The MQTT broker has been unreachable for 2 minutes."},
            "startsAt": "2026-09-24T10:00:00.000Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus:9090/graph?g0.expr=up%3D%3D0",
            "fingerprint": "abc123",
        },
        {
            "status": "resolved",
            "labels": {"alertname": "NucDiskFull", "severity": "warning"},
            "annotations": {"description": "Disk usage is back under 90%."},
            "startsAt": "2026-09-24T09:00:00.000Z",
            "endsAt": "2026-09-24T09:30:00.000Z",
        },
    ],
}


@pytest.fixture
def recording_notifier(engine, monkeypatch: pytest.MonkeyPatch):
    recorder = RecordingNotifier()
    monkeypatch.setattr(state_module, "build_notifier", lambda **kwargs: recorder)
    with TestClient(app) as client:
        yield client, recorder


def _wait_for_sent(recorder: RecordingNotifier, count: int) -> list[Notification]:
    deadline = time.monotonic() + 5
    while len(recorder.sent) < count and time.monotonic() < deadline:
        time.sleep(0.02)
    return recorder.sent


def test_alerts_require_the_admin_token(client: TestClient) -> None:
    assert client.post("/admin/alerts", json=V4_PAYLOAD).status_code == 401


def test_each_alert_is_delivered_as_a_text_notification(recording_notifier) -> None:
    client, recorder = recording_notifier
    firing_before = metrics.counter(ALERTS_RECEIVED, status="firing")
    resolved_before = metrics.counter(ALERTS_RECEIVED, status="resolved")

    resp = client.post("/admin/alerts", json=V4_PAYLOAD, headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"accepted": 2}

    sent = _wait_for_sent(recorder, 2)
    assert [n.title for n in sent] == ["🚨 MosquittoDown", "✅ Resolved: NucDiskFull"]
    assert sent[0].message == "The MQTT broker has been unreachable for 2 minutes. (mosquitto)"
    assert sent[0].priority == "high"  # critical + firing
    assert sent[1].message == "Disk usage is back under 90%."
    assert sent[1].priority == "default"
    assert metrics.counter(ALERTS_RECEIVED, status="firing") - firing_before == 1
    assert metrics.counter(ALERTS_RECEIVED, status="resolved") - resolved_before == 1


def test_a_malformed_payload_is_422_and_delivers_nothing(recording_notifier) -> None:
    client, recorder = recording_notifier
    assert (
        client.post("/admin/alerts", json={"alerts": "nope"}, headers=ADMIN_HEADERS).status_code
        == 422
    )
    assert (
        client.post(
            "/admin/alerts",
            json={"alerts": [{"status": "exploding", "labels": {}}]},
            headers=ADMIN_HEADERS,
        ).status_code
        == 422
    )
    too_many = {"alerts": [{"status": "firing", "labels": {"alertname": "x"}}] * 101}
    assert client.post("/admin/alerts", json=too_many, headers=ADMIN_HEADERS).status_code == 422
    time.sleep(0.1)
    assert recorder.sent == []


def test_an_empty_batch_is_accepted(recording_notifier) -> None:
    client, _ = recording_notifier
    resp = client.post("/admin/alerts", json={"alerts": []}, headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 0}
