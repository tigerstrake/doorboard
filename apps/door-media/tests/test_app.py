import uuid

from doorboard_contracts.events import SessionState
from fastapi.testclient import TestClient


def test_health(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "door-media"
    assert data["mode"] == "mock"


def test_metrics(client: TestClient):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "door_media_uptime_s" in resp.text


def test_streams(client: TestClient):
    resp = client.get("/streams")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "visitor"
    assert data[0]["stream_up"] is True


def test_session_event_lifecycle(client: TestClient):
    session_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())

    # 1. Trigger bell recording
    resp = client.post(
        "/internal/session_event",
        json={
            "session_id": session_id,
            "from_state": SessionState.IDLE,
            "to_state": SessionState.BUTTON_PRESSED,
            "trigger": "test",
            "trace_id": trace_id,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True

    # Let async tasks run
    time = __import__("time")
    time.sleep(0.1)

    # Verify in DB
    resp = client.get("/recordings")
    assert resp.status_code == 200
    recordings = resp.json()
    assert len(recordings) == 1
    assert recordings[0]["session_id"] == session_id
    assert recordings[0]["kind"] == "bell_clip"
    assert recordings[0]["path"] is None  # not finalized

    # 2. Trigger finalize
    resp = client.post(
        "/internal/session_event",
        json={
            "session_id": session_id,
            "from_state": SessionState.VISITOR_MODE,
            "to_state": SessionState.SESSION_END,
            "trigger": "test",
            "trace_id": trace_id,
        },
    )
    assert resp.status_code == 200
    time.sleep(0.1)

    resp = client.get("/recordings")
    recordings = resp.json()
    assert recordings[0]["path"] is not None

    recording_id = recordings[0]["recording_id"]
    sha256 = recordings[0]["sha256"]

    # 3. Mark synced
    resp = client.post(
        "/internal/sync_completed",
        json={
            "recording_id": recording_id,
            "verified_sha256": sha256,
            "item_id": str(uuid.uuid4()),
            "attempts": 1,
        },
    )
    assert resp.status_code == 200

    resp = client.get("/recordings")
    assert resp.json()[0]["sync_status"] == "synced"

    # 4. Delete
    resp = client.delete(f"/recordings/{recording_id}")
    assert resp.status_code == 200

    resp = client.get("/recordings")
    assert len(resp.json()) == 0
