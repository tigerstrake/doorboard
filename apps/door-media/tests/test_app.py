import uuid
from typing import Any, cast

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
    recordings = resp.json()["recordings"]
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
    recordings = resp.json()["recordings"]
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
    assert resp.json()["recordings"][0]["sync_status"] == "synced"

    # 4. Delete
    resp = client.delete(f"/recordings/{recording_id}")
    assert resp.status_code == 200

    resp = client.get("/recordings")
    assert len(resp.json()["recordings"]) == 0


def test_video_message_discard_deletes_finalized_clip(client: TestClient):
    session_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    time = __import__("time")

    start = client.post(
        "/internal/session_event",
        json={
            "session_id": session_id,
            "from_state": SessionState.VIDEO_MESSAGE_OFFERED,
            "to_state": SessionState.VIDEO_MESSAGE_RECORDING,
            "trigger": "visitor:record_start",
            "trace_id": trace_id,
        },
    )
    assert start.status_code == 200
    time.sleep(0.1)

    review = client.post(
        "/internal/session_event",
        json={
            "session_id": session_id,
            "from_state": SessionState.VIDEO_MESSAGE_RECORDING,
            "to_state": SessionState.VIDEO_MESSAGE_REVIEW,
            "trigger": "visitor:record_stop",
            "trace_id": trace_id,
        },
    )
    assert review.status_code == 200
    time.sleep(0.1)

    recordings = client.get("/recordings").json()["recordings"]
    assert len(recordings) == 1
    recording = recordings[0]
    assert recording["kind"] == "video_message"
    assert recording["consent_context"] == "visitor_initiated"
    assert recording["thumbnail_path"] is not None
    file_response = client.get(
        f"/recordings/{recording['recording_id']}/file?session_id={session_id}"
    )
    assert file_response.status_code == 200

    discard = client.post(
        "/internal/session_event",
        json={
            "session_id": session_id,
            "from_state": SessionState.VIDEO_MESSAGE_REVIEW,
            "to_state": SessionState.SESSION_END,
            "trigger": "visitor:discard",
            "trace_id": trace_id,
        },
    )
    assert discard.status_code == 200
    time.sleep(0.1)

    assert client.get("/recordings").json()["recordings"] == []
    missing = client.get(f"/recordings/{recording['recording_id']}/file?session_id={session_id}")
    assert missing.status_code == 404


def test_photo_booth_save_writes_consent_metadata(client: TestClient):
    session_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())

    capture = client.post(
        "/photos/capture",
        json={"session_id": session_id, "trace_id": trace_id},
    )
    assert capture.status_code == 200
    photo = capture.json()["photo"]

    save = client.post(
        f"/photos/{photo['recording_id']}/save",
        json={"session_id": session_id, "trace_id": trace_id},
    )
    assert save.status_code == 200
    recording = save.json()["recording"]
    assert recording["kind"] == "photo_booth"
    assert recording["consent_context"] == "visitor_initiated"
    assert recording["consent_metadata_path"] is not None

    row = client.get("/recordings", params={"kind": "photo_booth"}).json()["recordings"][0]
    assert row["recording_id"] == photo["recording_id"]
    assert row["thumbnail_path"] is not None
    assert row["consent_metadata_path"] == recording["consent_metadata_path"]

    cfg = cast(Any, client.app).state.cfg
    metadata = (cfg.ssd_data_root / row["consent_metadata_path"]).read_text(encoding="utf-8")
    assert '"capture_mode": "explicit_photo_booth"' in metadata
    assert '"saved_after_review": true' in metadata


def test_photo_booth_discard_leaves_no_media_files(client: TestClient):
    session_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())

    capture = client.post(
        "/photos/capture",
        json={"session_id": session_id, "trace_id": trace_id},
    )
    assert capture.status_code == 200
    photo = capture.json()["photo"]
    cfg = cast(Any, client.app).state.cfg
    assert (cfg.ssd_data_root / photo["review_path"]).exists()

    discard = client.post(
        f"/photos/{photo['recording_id']}/discard",
        json={"session_id": session_id, "trace_id": trace_id},
    )
    assert discard.status_code == 200
    assert not (cfg.ssd_data_root / photo["review_path"]).exists()
    assert client.get("/recordings", params={"kind": "photo_booth"}).json()["recordings"] == []
    assert list((cfg.ssd_data_root / "recordings").rglob("photo_booth_*")) == []
    assert list((cfg.ssd_data_root / "thumbnails").rglob("photo_booth_*")) == []
