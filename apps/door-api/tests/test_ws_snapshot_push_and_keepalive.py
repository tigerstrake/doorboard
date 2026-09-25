"""What a connected kiosk is told, and when (2026-09 audit, lanes A1/E1/E2).

Three gaps in the ``/ws`` hub, all about a client that is *already* connected:

* When a recognised identity lapsed, the sweeper only rewrote the snapshot handed to
  the next client. A kiosk on the socket kept the name — the one server-side "stop
  showing this" signal never reached a live screen. Same for privacy mode.
* ``on_event`` built the stored snapshot before the machine cleared the session's ids on
  the IDLE transition, so every client connecting afterwards received the previous
  visitor's name with state IDLE. A wallboard reload after Tiger left greeted "Hi Tiger"
  at whoever was in the corridor.
* door-api only sent frames when something happened, and the event client declares an
  OPEN socket stale after 45 s of silence. With the NUC quiet, the wallboard reconnected
  every cycle all night and the doorpad was yanked back to its default screen each time.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Generator
from typing import Any

import pytest
from doorboard_contracts import SessionState
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state  # noqa: E402
from door_api.recognised_identity import RecognisedIdentity  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    state.__init__()
    state.startup()
    yield
    state.shutdown()


def _recognise() -> None:
    state.machine.handle_identity_stable(
        person_id="prs_alex", display_name="Alex", profile_id="blue_wave", consent_version="v3"
    )
    assert state.session_snapshot_dict()["display_name"] == "Alex"


def _drain(queue: asyncio.Queue[str]) -> list[dict[str, Any]]:
    out = []
    while not queue.empty():
        out.append(json.loads(queue.get_nowait()))
    return out


# -- identity release reaches live kiosks -----------------------------------------


@pytest.mark.anyio
async def test_a_lapsed_identity_is_pushed_to_connected_clients_within_one_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOOR_API_IDENTITY_SWEEP_INTERVAL_S", "0.02")
    state.__init__()
    state.startup()  # inside a running loop, so the sweeper task exists
    clock = {"t": 1000.0}
    state.identity = RecognisedIdentity(
        idle_ttl_s=state.config.recognised_identity_idle_ttl_s,
        interaction_ttl_s=state.config.recognised_identity_interaction_ttl_s,
        monotonic_fn=lambda: clock["t"],
    )
    try:
        _recognise()
        # The approach session lapses long before the holder does (10 s vs 33 s); the
        # name on screen is then the holder's alone, which is the audit's scenario.
        state.machine.handle_identity_expired(person_id="prs_alex")
        assert state.machine.state == SessionState.IDLE
        assert state.session_snapshot_dict()["display_name"] == "Alex"
        queue = state.broadcast.make_client_queue()
        _drain(queue)  # the connect-time snapshot, which still names Alex
        await asyncio.sleep(0.05)  # let a sweep see the name held

        clock["t"] += state.config.recognised_identity_idle_ttl_s + 1.0
        await asyncio.sleep(0.1)  # a few sweeps

        frames = _drain(queue)
        snapshots = [f for f in frames if f["type"] == "snapshot"]
        assert snapshots, f"no snapshot pushed to the connected client: {frames}"
        assert snapshots[-1]["state"]["display_name"] is None
        assert snapshots[-1]["state"]["attributed_to"] is None
    finally:
        state.broadcast.remove_client(queue)
        state.shutdown()


def test_privacy_mode_pushes_the_cleared_snapshot_to_connected_clients() -> None:
    from doorboard_contracts.events import parse_event
    from doorboard_contracts.examples import example_event

    _recognise()
    queue = state.broadcast.make_client_queue()
    _drain(queue)
    try:
        privacy_on = example_event("vision.privacy_mode_changed").model_dump(mode="json")
        privacy_on["payload"]["enabled"] = True
        state.handle_contract_event(parse_event(privacy_on))

        snapshots = [f for f in _drain(queue) if f["type"] == "snapshot"]
        assert snapshots, "the live kiosk was not told the name is gone"
        assert snapshots[-1]["state"]["display_name"] is None
    finally:
        state.broadcast.remove_client(queue)


# -- no stale snapshot after IDLE ----------------------------------------------------


def test_a_client_connecting_after_the_session_ended_is_not_handed_the_last_visitor() -> None:
    _recognise()
    state.machine.handle_button_pressed()
    assert state.machine.state == SessionState.VISITOR_MODE
    # The holder has gone (the audit scenario: the session outlives the identity window).
    state.identity.forget()

    assert state.machine.handle_session_end(trigger="visitor:end")
    assert state.machine.transition(SessionState.IDLE, "auto:end_to_idle")

    queue = state.broadcast.make_client_queue()
    try:
        first = _drain(queue)[0]
        assert first["type"] == "snapshot"
        assert first["state"]["state"] == "IDLE"
        assert first["state"]["session_id"] is None
        assert first["state"]["person_id"] is None
        assert first["state"]["display_name"] is None
    finally:
        state.broadcast.remove_client(queue)


def test_a_timer_driven_idle_also_leaves_a_clean_snapshot() -> None:
    # The audit's exact path: the SESSION_END -> IDLE linger timer, not a route.
    _recognise()
    state.machine.handle_button_pressed()
    state.identity.forget()
    state.machine.handle_session_end(trigger="visitor:end")

    async def run() -> None:
        # Fire the linger timer's transition the way the timer would.
        state.machine.transition(SessionState.IDLE, "auto:end_to_idle")

    asyncio.run(run())
    queue = state.broadcast.make_client_queue()
    try:
        first = _drain(queue)[0]
        assert first["state"] == {**first["state"], "session_id": None, "display_name": None}
    finally:
        state.broadcast.remove_client(queue)


# -- keepalive ------------------------------------------------------------------------


def test_a_quiet_door_pings_inside_the_client_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOR_API_WS_PING_INTERVAL_S", "0.05")
    state.__init__()
    state.startup()
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        assert json.loads(websocket.receive_text())["type"] == "snapshot"
        started = time.monotonic()
        frame = json.loads(websocket.receive_text())
        elapsed = time.monotonic() - started

    assert frame["type"] == "ping"
    assert isinstance(frame["ts"], int)
    assert elapsed < 1.0, f"ping took {elapsed:.2f}s on a 0.05s interval"


def test_the_default_ping_interval_sits_inside_the_event_client_watchdog() -> None:
    # packages/event-client/src/index.ts: staleTimeoutMs defaults to 45 000. Restated
    # here on purpose so the two cannot drift back into a gap unnoticed.
    event_client_stale_after_s = 45.0
    assert 0 < state.config.ws_ping_interval_s <= event_client_stale_after_s / 2
