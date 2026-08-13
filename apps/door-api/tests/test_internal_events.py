"""The ingest that carries a recognised identity from door-visiond to the screen.

door-visiond has always emitted ``vision.identity_stable``; nothing carried it out of
that process, so the session machine never left ``IDLE`` and the wallboard greeted
nobody (ADR-0018 §3). These tests pin both halves of that: the event now moves the
machine, and the route cannot be used to assert anything else about the door.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest
from doorboard_contracts import SessionState
from doorboard_contracts.examples import example_event
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state

TOKEN = "internal-event-token-for-tests"


@pytest.fixture(autouse=True)
def _app_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_INTERNAL_EVENT_TOKEN", TOKEN)
    state.__init__()
    state.startup()
    yield
    state.shutdown()


def _event(event_type: str) -> dict[str, Any]:
    return example_event(event_type).model_dump(mode="json")


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_identity_stable_reaches_the_session_machine() -> None:
    """The regression that mattered: recognition now changes what the screen shows."""
    client = TestClient(app)
    assert state.machine.state == SessionState.IDLE

    response = client.post(
        "/internal/events", json=_event("vision.identity_stable"), headers=_auth()
    )

    assert response.status_code == 202
    assert response.json()["changed"] is True
    # APPROACH_DETECTED is what ApproachGreeting renders on; IDLE shows nothing.
    assert state.machine.state == SessionState.APPROACH_DETECTED
    # The name the greeting renders travels on the session snapshot the kiosks read.
    assert state.session_snapshot_dict()["display_name"] == "Alex"


def test_identity_expired_returns_the_session_to_idle() -> None:
    client = TestClient(app)
    client.post("/internal/events", json=_event("vision.identity_stable"), headers=_auth())

    response = client.post(
        "/internal/events", json=_event("vision.identity_expired"), headers=_auth()
    )

    assert response.status_code == 202
    assert state.machine.state == SessionState.IDLE


def test_a_no_op_event_is_still_accepted() -> None:
    """The caller is fire-and-forget: 'changed nothing' must not read as retry-me."""
    client = TestClient(app)

    response = client.post("/internal/events", json=_event("vision.face_visible"), headers=_auth())

    assert response.status_code == 202
    assert response.json()["changed"] is False


def test_route_fails_closed_without_a_configured_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOOR_API_INTERNAL_EVENT_TOKEN", raising=False)
    state.__init__()
    state.startup()
    client = TestClient(app)

    response = client.post("/internal/events", json=_event("vision.identity_stable"))

    assert response.status_code == 503
    assert state.machine.state == SessionState.IDLE


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-token"}, {"Authorization": TOKEN}],
    ids=["missing", "wrong", "unprefixed"],
)
def test_unauthenticated_callers_cannot_assert_an_identity(headers: dict[str, str]) -> None:
    """Identity is the one claim personalisation reads, so this cannot be open.

    Loopback binding is not the control: the kiosk browsers run on this same Pi and
    can reach 127.0.0.1:8080 from any page they load.
    """
    client = TestClient(app)

    response = client.post(
        "/internal/events", json=_event("vision.identity_stable"), headers=headers
    )

    assert response.status_code == 401
    assert state.machine.state == SessionState.IDLE


@pytest.mark.parametrize(
    "event_type",
    ["door.button_pressed", "door.contact_changed", "session.state_changed"],
)
def test_only_vision_events_are_accepted(event_type: str) -> None:
    """A token holder still cannot fake the door's own inputs.

    Button presses and contact changes arrive over the ESP32 link, which is the trust
    boundary that gives them meaning; this route exists for recognition alone.
    """
    client = TestClient(app)

    response = client.post("/internal/events", json=_event(event_type), headers=_auth())

    assert response.status_code == 403
    assert state.machine.state == SessionState.IDLE


def test_malformed_envelope_is_rejected_not_crashed() -> None:
    client = TestClient(app)

    response = client.post(
        "/internal/events",
        json={"type": "vision.identity_stable", "payload": {"person_id": "prs_1"}},
        headers=_auth(),
    )

    assert response.status_code == 422
    assert state.machine.state == SessionState.IDLE
