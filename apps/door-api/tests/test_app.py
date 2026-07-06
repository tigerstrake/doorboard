"""Smoke test for the door-api ASGI app."""

from __future__ import annotations

import json
import os
from collections.abc import Generator

import pytest
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
