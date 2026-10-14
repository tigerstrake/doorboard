"""Body Surfers start/stop/clear on the wallboard (ADR-0049).

The DoorPad starts and stops the game; door-api only fans an ephemeral
``wallboard.game_changed`` message out on ``/ws``, the same way it routes tile focus.
Two refusals matter more than the happy path: no start while privacy mode is on (the
game tracks bodies on the visitor camera) and no start over a visitor session (the bell
always wins). Clearing the leaderboard deletes crash selfies, so it is owner-only.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Generator
from typing import Any

import pytest
from doorboard_contracts import SessionState
from doorboard_contracts.events import parse_event
from doorboard_contracts.examples import example_event
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

import door_api.app as door_api_app  # noqa: E402
from door_api.app import app, state  # noqa: E402

ADMIN = {"Authorization": "Bearer owner-token"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_ADMIN_TOKEN", "owner-token")
    monkeypatch.delenv("FEATURE_BODY_SURFERS", raising=False)
    state.__init__()
    state.startup()
    yield
    state.shutdown()


@pytest.fixture(autouse=True)
def _visiond_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """door-visiond is not running in tests: the probe falls back to the tracked copy."""

    async def _no_answer() -> bool | None:
        return None

    monkeypatch.setattr(door_api_app, "_visiond_privacy_enabled", _no_answer)


def _restart_with(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    state.shutdown()
    state.__init__()
    state.startup()


def _receive_delta(websocket: Any, event_type: str) -> dict[str, Any]:
    while True:
        data = json.loads(websocket.receive_text())
        if data.get("type") == "delta" and data.get("event", {}).get("type") == event_type:
            return data["event"]


def _drain(queue: asyncio.Queue[str]) -> list[dict[str, Any]]:
    out = []
    while not queue.empty():
        out.append(json.loads(queue.get_nowait()))
    return out


def _game_deltas(queue: asyncio.Queue[str]) -> list[dict[str, Any]]:
    return [
        frame["event"]
        for frame in _drain(queue)
        if frame["type"] == "delta" and frame["event"].get("type") == "wallboard.game_changed"
    ]


def _privacy(enabled: bool) -> None:
    event = example_event("vision.privacy_mode_changed").model_dump(mode="json")
    event["payload"]["enabled"] = enabled
    state.handle_contract_event(parse_event(event))


def test_start_broadcasts_the_exact_game_message() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        json.loads(websocket.receive_text())  # snapshot

        response = client.post("/wallboard/game", json={"action": "start"})

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        game = body["game"]
        assert set(game) == {"type", "game", "action", "reason", "requestId", "requestedAt"}
        assert game["type"] == "wallboard.game_changed"
        assert game["game"] == "body_surfers"
        assert game["action"] == "start"
        assert game["reason"] == "doorpad"
        assert isinstance(game["requestId"], str) and game["requestId"]
        assert isinstance(game["requestedAt"], int)

        assert _receive_delta(websocket, "wallboard.game_changed") == game


def test_stop_reported_by_the_wallboard_carries_reason_ended() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        json.loads(websocket.receive_text())  # snapshot

        response = client.post("/wallboard/game", json={"action": "stop", "reason": "ended"})

        assert response.status_code == 200
        event = _receive_delta(websocket, "wallboard.game_changed")
        assert event["action"] == "stop"
        assert event["reason"] == "ended"


def test_unknown_action_or_reason_is_rejected() -> None:
    client = TestClient(app)
    assert client.post("/wallboard/game", json={"action": "pause"}).status_code == 422
    assert (
        client.post("/wallboard/game", json={"action": "stop", "reason": "admin"}).status_code
        == 422
    )


def test_feature_off_hides_both_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    _restart_with(monkeypatch, FEATURE_BODY_SURFERS="false")
    client = TestClient(app)

    start = client.post("/wallboard/game", json={"action": "start"})
    assert start.status_code == 404
    assert start.json()["detail"] == "body_surfers_disabled"

    clear = client.post("/admin/wallboard/game/clear-leaderboard", headers=ADMIN)
    assert clear.status_code == 404
    assert clear.json()["detail"] == "body_surfers_disabled"


def test_no_start_while_privacy_mode_is_on() -> None:
    client = TestClient(app)
    _privacy(True)

    response = client.post("/wallboard/game", json={"action": "start"})
    assert response.status_code == 409
    assert response.json()["detail"] == "privacy_mode"

    # A stop is always accepted, so the DoorPad can never get stuck on "playing".
    assert client.post("/wallboard/game", json={"action": "stop"}).status_code == 200

    _privacy(False)
    assert client.post("/wallboard/game", json={"action": "start"}).status_code == 200


def test_privacy_mode_switching_on_broadcasts_a_stop() -> None:
    queue = state.broadcast.make_client_queue()
    _drain(queue)
    try:
        _privacy(True)
        stops = _game_deltas(queue)
        assert len(stops) == 1
        assert stops[0]["action"] == "stop"
        assert stops[0]["reason"] == "privacy_mode"
        assert stops[0]["game"] == "body_surfers"

        # Already on: a repeat event is not a new reason to stop anything.
        _privacy(True)
        assert _game_deltas(queue) == []
        # Switching it off starts nothing.
        _privacy(False)
        assert _game_deltas(queue) == []
    finally:
        state.broadcast.remove_client(queue)


def test_privacy_mode_with_the_feature_off_broadcasts_no_game_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _restart_with(monkeypatch, FEATURE_BODY_SURFERS="false")
    queue = state.broadcast.make_client_queue()
    _drain(queue)
    try:
        _privacy(True)
        assert _game_deltas(queue) == []
        assert state.privacy_mode_enabled is True
    finally:
        state.broadcast.remove_client(queue)


def test_no_start_over_a_visitor_session() -> None:
    client = TestClient(app)
    state.handle_contract_event(example_event("door.button_pressed"))
    assert state.machine.state == SessionState.VISITOR_MODE

    response = client.post("/wallboard/game", json={"action": "start"})
    assert response.status_code == 409
    assert response.json()["detail"] == "visitor_session"

    assert client.post("/wallboard/game", json={"action": "stop"}).status_code == 200


def test_clear_leaderboard_is_owner_only() -> None:
    client = TestClient(app)

    assert client.post("/admin/wallboard/game/clear-leaderboard").status_code == 401
    wrong = {"Authorization": "Bearer not-the-owner"}
    assert client.post("/admin/wallboard/game/clear-leaderboard", headers=wrong).status_code == 401


def test_clear_leaderboard_broadcasts_to_the_wallboard() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        json.loads(websocket.receive_text())  # snapshot

        response = client.post("/admin/wallboard/game/clear-leaderboard", headers=ADMIN)

        assert response.status_code == 200
        game = response.json()["game"]
        assert game["action"] == "clear_leaderboard"
        assert game["reason"] == "admin"
        assert _receive_delta(websocket, "wallboard.game_changed") == game


def test_start_asks_visiond_so_a_restart_cannot_forget_privacy_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No privacy event since this door-api started, but door-visiond has it on.
    async def _privacy_on() -> bool | None:
        return True

    monkeypatch.setattr(door_api_app, "_visiond_privacy_enabled", _privacy_on)
    client = TestClient(app)
    response = client.post("/wallboard/game", json={"action": "start"})
    assert response.status_code == 409
    assert response.json()["detail"] == "privacy_mode"
    assert state.privacy_mode_enabled is True


def test_visiond_saying_privacy_is_off_lets_a_start_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state.privacy_mode_enabled = True

    async def _privacy_off() -> bool | None:
        return False

    monkeypatch.setattr(door_api_app, "_visiond_privacy_enabled", _privacy_off)
    client = TestClient(app)
    assert client.post("/wallboard/game", json={"action": "start"}).status_code == 200
