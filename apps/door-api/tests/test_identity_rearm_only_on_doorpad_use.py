"""Only doorpad *use* re-arms the recognised identity, never a read (ADR-0020).

The bug this pins, found in the 2026-09 audit: the interaction middleware re-armed the
identity window on *any* request under the interaction prefixes, ``GET`` included. The
wallboard polls ``GET /guestbook`` and ``GET /polls/current`` every 30 s with nobody at the
door, which is inside the 120 s interaction window, so a recognised identity never expired
— and every later stranger's note, vote and check-in was attributed to whoever was last
recognised (ADR-0018 §2, E-25). door-api cannot tell the two kiosks apart; it can tell a
read from a write, and every doorpad action is a write.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state  # noqa: E402
from door_api.recognised_identity import RecognisedIdentity  # noqa: E402

# The wallboard's ambient poll cadence (apps/door-ui/src/App.tsx, route /wallboard).
WALLBOARD_POLL_S = 30.0
SIMULATED_WINDOW_S = 600.0


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Generator[dict[str, float], None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    state.__init__()
    state.startup()
    fake = {"t": 1000.0}
    # Same TTLs as production, on a clock the test controls.
    state.identity = RecognisedIdentity(
        idle_ttl_s=state.config.recognised_identity_idle_ttl_s,
        interaction_ttl_s=state.config.recognised_identity_interaction_ttl_s,
        monotonic_fn=lambda: fake["t"],
    )
    yield fake
    state.shutdown()


def _recognise() -> None:
    state.machine.handle_identity_stable(
        person_id="prs_alex", display_name="Alex", profile_id="blue_wave", consent_version="v3"
    )
    held = state.identity.current()
    assert held is not None and held.person_id == "prs_alex"


def _poll_like_the_wallboard(client: TestClient, clock: dict[str, float]) -> None:
    for _ in range(int(SIMULATED_WINDOW_S / WALLBOARD_POLL_S)):
        clock["t"] += WALLBOARD_POLL_S
        assert client.get("/guestbook?limit=5").status_code == 200
        assert client.get("/polls/current").status_code == 200


def test_the_wallboard_ambient_poll_does_not_hold_the_identity(clock: dict[str, float]) -> None:
    client = TestClient(app)
    _recognise()
    assert state.config.recognised_identity_interaction_ttl_s > WALLBOARD_POLL_S, (
        "the scenario only bites when the poll lands inside the interaction window"
    )

    _poll_like_the_wallboard(client, clock)

    assert state.identity.current() is None, (
        "ten minutes of wallboard reads with nobody at the door kept the identity alive"
    )
    assert state.identity.seconds_remaining() == 0.0
    assert state.session_snapshot_dict()["attributed_to"] is None


def test_using_the_doorpad_does_hold_the_identity(clock: dict[str, float]) -> None:
    """The keepalive the doorpad sends on touch (ADR-0020) must still work."""
    client = TestClient(app)
    _recognise()

    for _ in range(int(SIMULATED_WINDOW_S / WALLBOARD_POLL_S)):
        clock["t"] += WALLBOARD_POLL_S
        assert client.post("/doorpad/activity").status_code == 200

    held = state.identity.current()
    assert held is not None and held.person_id == "prs_alex"
    assert state.identity.seconds_remaining() > 0.0


def test_a_stranger_after_the_window_is_not_attributed_to_the_last_person(
    clock: dict[str, float],
) -> None:
    """The consequence that matters: nobody inherits Alex's name."""
    client = TestClient(app)
    _recognise()
    _poll_like_the_wallboard(client, clock)
    assert state.identity.current() is None

    # A stranger rings and leaves a note on the doorpad.
    assert client.post("/doorpad/ring").status_code == 200
    token = client.get("/visitor-token").json()["token"]
    resp = client.post("/guestbook", json={"text": "stranger note", "session_token": token})
    assert resp.status_code == 201, resp.text

    entry = state.social_store.get_guestbook_entry(resp.json()["id"])
    assert entry is not None
    assert entry.person_id is None
    assert client.get("/visitor-session", params={"token": token}).json()["attributed_to"] is None
