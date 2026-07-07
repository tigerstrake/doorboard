"""Smoke test for the door-api ASGI app."""

from __future__ import annotations

import json
import os
from collections.abc import Generator

import pytest
from doorboard_simulator.clock import SimClock
from doorboard_simulator.events import EventFactory
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state


@pytest.fixture(autouse=True)
def _mock_env_for_test(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    # Force the app to use an in-memory DB for tests, avoiding the RuntimeError
    # from config.py if the environment isn't fully set up.
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    # Re-initialize state to pick up the test env
    state.__init__()
    state.startup()
    yield
    state.shutdown()


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_metrics() -> None:
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    data = response.json()
    assert "session_transitions_total" in data


def test_websocket_broadcast_smoke() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        # First message should be the snapshot.
        data = websocket.receive_text()
        snapshot = json.loads(data)
        assert snapshot["type"] == "snapshot"
        assert snapshot["state"]["state"] == "IDLE"

        # Trigger a transition on the machine directly.
        state.machine.handle_button_pressed()

        # The machine will emit multiple events (BUTTON_PRESSED then VISITOR_MODE).
        # And we get deltas for them.

        # Read the state_changed for BUTTON_PRESSED
        delta1_raw = websocket.receive_text()
        delta1 = json.loads(delta1_raw)
        assert delta1["type"] == "delta"
        assert delta1["event"]["type"] == "session.state_changed"
        assert delta1["event"]["payload"]["to_state"] == "BUTTON_PRESSED"

        # Read the session.started
        delta2_raw = websocket.receive_text()
        delta2 = json.loads(delta2_raw)
        assert delta2["type"] == "delta"
        assert delta2["event"]["type"] == "session.started"

        # Read the state_changed for VISITOR_MODE
        delta3_raw = websocket.receive_text()
        delta3 = json.loads(delta3_raw)
        assert delta3["type"] == "delta"
        assert delta3["event"]["type"] == "session.state_changed"
        assert delta3["event"]["payload"]["to_state"] == "VISITOR_MODE"


def _receive_delta(websocket, event_type: str) -> dict:
    while True:
        data = json.loads(websocket.receive_text())
        if data.get("type") == "delta" and data.get("event", {}).get("type") == event_type:
            return data["event"]


def test_doorpad_ring_uses_touch_entry_and_effect_event() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        json.loads(websocket.receive_text())  # snapshot
        response = client.post("/doorpad/ring")

        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["effect"]["status"] == "unavailable"
        assert body["session"]["state"] == "VISITOR_MODE"

        changed = _receive_delta(websocket, "session.state_changed")
        started = _receive_delta(websocket, "session.started")
        effect = _receive_delta(websocket, "door.effect_play")

        assert changed["payload"]["trigger"] == "doorpad.touch_ring"
        assert started["payload"]["entry"] == "touch"
        assert effect["payload"]["effect_id"] == state.config.doorpad_effect_id


def test_video_message_offer_starts_touch_session() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        json.loads(websocket.receive_text())  # snapshot
        response = client.post("/doorpad/video-message/offer")

        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["session"]["state"] == "VIDEO_MESSAGE_OFFERED"

        first_changed = _receive_delta(websocket, "session.state_changed")
        started = _receive_delta(websocket, "session.started")
        assert first_changed["payload"]["trigger"] == "doorpad.video_message_offer"
        assert started["payload"]["entry"] == "touch"


def test_visitor_token_requires_active_session_and_is_scoped() -> None:
    client = TestClient(app)
    missing = client.get("/visitor-token")
    assert missing.status_code == 409

    client.post("/doorpad/ring")
    response = client.get("/visitor-token")

    assert response.status_code == 200
    token = response.json()["token"]
    assert "." in token
    assert response.json()["url"].endswith(f"/visitor?token={token}")


def test_contract_button_event_sets_cached_profile_snapshot() -> None:
    events = EventFactory(SimClock())
    button = events.make(
        "door.button_pressed",
        {
            "press_id": "00000000-0000-0000-0000-000000000001",
            "had_cached_profile": True,
            "profile_id": "blue_wave",
        },
    )

    accepted = state.handle_contract_event(button)

    assert accepted is True
    snapshot = state.snapshot_response()["session"]
    assert snapshot["state"] == "VISITOR_MODE"
    assert snapshot["had_cached_profile"] is True
    assert snapshot["profile_id"] == "blue_wave"
