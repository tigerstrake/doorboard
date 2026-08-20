"""The ingest that carries a recognised identity from door-visiond to the screen.

door-visiond has always emitted ``vision.identity_stable``; nothing carried it out of
that process, so the session machine never left ``IDLE`` and the wallboard greeted
nobody (ADR-0018 §3). These tests pin both halves of that: the event now moves the
machine, and the route cannot be used to assert anything else about the door.
"""

from __future__ import annotations

import json
import os
import time
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


# ---------------------------------------------------------------------------
# Why an identity expired decides whether the name comes off the screen (ADR-0029)
# ---------------------------------------------------------------------------


def _expired_event(reason: str | None) -> dict[str, Any]:
    event = _event("vision.identity_expired")
    event["payload"]["person_id"] = "prs_alex"
    if reason is None:
        event["payload"].pop("reason", None)
    else:
        event["payload"]["reason"] = reason
    return event


def _recognise_alex() -> None:
    state.machine.handle_identity_stable(
        person_id="prs_alex",
        display_name="Alex",
        profile_id="blue_wave",
        consent_version="v3",
    )
    assert state.identity.current() is not None


def test_a_face_leaving_the_frame_does_not_drop_the_held_name() -> None:
    # Faces leave frame constantly while someone stands at the doorpad looking down at it;
    # ADR-0020 exists because clearing on that made the greeting flicker off mid-visit.
    client = TestClient(app)
    _recognise_alex()

    response = client.post("/internal/events", json=_expired_event("expired"), headers=_auth())
    assert response.status_code == 202

    held = state.identity.current()
    assert held is not None
    assert held.display_name == "Alex"


def test_an_event_with_no_reason_is_treated_as_the_routine_case() -> None:
    # An older door omits the field. Failing toward "keep the name" is correct: the deletion
    # paths clear explicitly, so the default must be the harmless one.
    client = TestClient(app)
    _recognise_alex()

    response = client.post("/internal/events", json=_expired_event(None), headers=_auth())
    assert response.status_code == 202
    assert state.identity.current() is not None


@pytest.mark.parametrize("reason", ["admin", "privacy_mode"])
def test_unenrollment_or_privacy_mode_clears_the_name_over_the_wire(reason: str) -> None:
    """The deletion promise, end to end through the real endpoint.

    Before this, unenrolling someone left their name on the door until an unrelated timer
    lapsed — up to 33 s idle, or two minutes mid-interaction — while the door tells visitors
    in as many words that removal is immediate.
    """
    client = TestClient(app)
    _recognise_alex()

    response = client.post("/internal/events", json=_expired_event(reason), headers=_auth())
    assert response.status_code == 202
    assert state.identity.current() is None, f"{reason} left the name held"


# ---------------------------------------------------------------------------
# media.storage_status: emitted for months, delivered nowhere (T-337)
# ---------------------------------------------------------------------------


def test_storage_status_is_accepted_and_broadcast() -> None:
    """door-ui subscribes to this and had no transport carrying it.

    door-api forwards session events *to* door-media and nothing came back, so the wallboard's
    capacity card read "Waiting for a media.storage_status update; no capacity is being
    guessed." indefinitely — honest, and permanently uninformative.
    """
    client = TestClient(app)
    before = state.broadcast.client_count
    queue = state.broadcast.make_client_queue()
    try:
        event = _event("media.storage_status")
        response = client.post("/internal/events", json=event, headers=_auth())
        assert response.status_code == 202

        # Drain the snapshot, then look for the delta.
        seen: list[dict[str, Any]] = []
        while not queue.empty():
            seen.append(json.loads(queue.get_nowait()))
        deltas = [m for m in seen if m.get("type") == "delta"]
        types = [m["event"]["type"] for m in deltas]
        assert "media.storage_status" in types, "it was accepted but never reached /ws"
    finally:
        state.broadcast.remove_client(queue)
        assert state.broadcast.client_count == before


def test_the_session_machine_has_no_opinion_about_disk_space() -> None:
    # Pure pass-through: capacity telemetry must not move the door's state machine.
    client = TestClient(app)
    before = state.machine.state
    response = client.post("/internal/events", json=_event("media.storage_status"), headers=_auth())
    assert response.status_code == 202
    assert response.json()["changed"] is False
    assert state.machine.state == before


@pytest.mark.parametrize(
    "event_type",
    ["media.recording_finalized", "media.retention_deleted", "door.button_pressed"],
)
def test_the_route_stays_narrow(event_type: str) -> None:
    """Widened by one named type, not by prefix.

    The other media events assert that a recording exists or was deleted — a claim about
    durable state. And the door's own inputs stay on the ESP32 link, which is the trust
    boundary that gives them meaning.
    """
    client = TestClient(app)
    response = client.post("/internal/events", json=_event(event_type), headers=_auth())
    assert response.status_code == 403


def test_storage_status_also_leaves_the_door_for_home_assistant() -> None:
    """Two surfaces wait on this event, and it has to reach both.

    door-ui reads it over /ws. Home Assistant's "Doorboard Sync Status" entity reads
    `doorboard/media/storage_status`, which control-plane-api publishes only for events it has
    ingested — so the entity sat at unknown until this was queued for the sync outbox
    (door-sync -> control plane -> MQTT fan-out).
    """
    client = TestClient(app)
    drained_before = 0
    while state.store.next_sync_event(time.time()) is not None:
        drained_before += 1

    response = client.post("/internal/events", json=_event("media.storage_status"), headers=_auth())
    assert response.status_code == 202

    queued = state.store.next_sync_event(time.time())
    assert queued is not None, "nothing was queued for the control plane"
    assert queued.event["type"] == "media.storage_status"
    assert "free_bytes" in queued.event["payload"]


def test_it_carries_nothing_personal_off_the_door() -> None:
    # ARCHITECTURE.md §9: what leaves the door matters. Capacity telemetry is dull by design,
    # and this pins that it stays that way if the payload ever grows.
    client = TestClient(app)
    while state.store.next_sync_event(time.time()) is not None:
        pass
    client.post("/internal/events", json=_event("media.storage_status"), headers=_auth())

    queued = state.store.next_sync_event(time.time())
    assert queued is not None
    allowed = {"free_bytes", "queue_depth", "oldest_unsynced_s", "recording_allowed"}
    unexpected = set(queued.event["payload"]) - allowed
    assert not unexpected, f"new fields left the door unreviewed: {sorted(unexpected)}"
