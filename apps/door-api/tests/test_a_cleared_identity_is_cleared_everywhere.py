"""Privacy mode and unenrollment clear the name everywhere, not only in the holder.

door-api keeps a recognised identity in two places: the interaction-scoped holder
(ADR-0020), which privacy mode and an ``admin``/``privacy_mode`` expiry already cleared
(ADR-0029), and the session machine's own copy — persisted to ``session.sqlite`` and
stamped as ``display_name`` onto every later ``session.state_changed`` bound for the NUC
(ADR-0018 §4). The 2026-09 audit found the second copy survived both clears for the rest of
the session: recognise Tiger, press, switch recognition off, and ``/session`` still said
Tiger, the row still said Tiger, and the next transition told the control plane Tiger was
at the door. Up to ``inactivity_timeout_s`` (ten minutes) of it.

Also pinned: a restart forgets who is at the door (ADR-0020), rather than restoring a
name from disk that may now belong to nobody present.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from typing import Any
from uuid import uuid4

import pytest
from doorboard_contracts import SessionState
from doorboard_contracts.events import parse_event
from doorboard_contracts.examples import example_event

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import state  # noqa: E402
from door_api.config import SessionConfig  # noqa: E402
from door_api.persistence import PersistedSession, SessionStore  # noqa: E402
from door_api.session import SessionMachine  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    state.__init__()
    state.startup()
    yield
    state.shutdown()


def _vision_event(event_type: str, **payload: Any) -> Any:
    as_dict = example_event(event_type).model_dump(mode="json")
    as_dict["payload"] = {**as_dict["payload"], **payload}
    return parse_event(as_dict)


def _recognised_session() -> None:
    """Alex is recognised and presses the bell: both copies of the name are live."""
    state.handle_contract_event(
        _vision_event(
            "vision.identity_stable",
            person_id="prs_alex",
            display_name="Alex",
            profile_id="blue_wave",
            consent_version="v3",
        )
    )
    state.machine.handle_button_pressed()
    assert state.machine.state == SessionState.VISITOR_MODE
    assert state.session_snapshot_dict()["display_name"] == "Alex"
    row = state.store.load()
    assert row is not None and row.display_name == "Alex" and row.person_id == "prs_alex"


def _drain(queue: Any) -> list[dict[str, Any]]:
    out = []
    while not queue.empty():
        out.append(json.loads(queue.get_nowait()))
    return out


def _assert_cleared_everywhere() -> None:
    # The holder (already the case before) and the machine's copy (the fix).
    assert state.identity.current() is None
    assert state.session_snapshot_dict()["display_name"] is None
    assert state.session_snapshot_dict()["person_id"] is None
    assert state.machine.snapshot().consent_version is None
    # The row on the SSD.
    row = state.store.load()
    assert row is not None
    assert row.display_name is None
    assert row.person_id is None
    assert row.profile_id is None
    # And the next event bound for the NUC.
    queue = state.broadcast.make_client_queue()
    try:
        _drain(queue)
        assert state.machine.transition(SessionState.RINGING, "test:ring")
        changed = [
            m["event"]
            for m in _drain(queue)
            if m["type"] == "delta" and m["event"]["type"] == "session.state_changed"
        ]
        assert changed, "no session.state_changed was broadcast"
        assert changed[-1]["payload"]["display_name"] is None
    finally:
        state.broadcast.remove_client(queue)


def test_privacy_mode_clears_the_name_from_the_session_the_row_and_the_next_event() -> None:
    _recognised_session()
    state.handle_contract_event(_vision_event("vision.privacy_mode_changed", enabled=True))
    _assert_cleared_everywhere()


@pytest.mark.parametrize("reason", ["admin", "privacy_mode"])
def test_a_deletion_expiry_clears_the_name_everywhere(reason: str) -> None:
    _recognised_session()
    state.handle_contract_event(
        _vision_event("vision.identity_expired", person_id="prs_alex", reason=reason)
    )
    _assert_cleared_everywhere()


def test_a_routine_expiry_keeps_the_name() -> None:
    # The whole reason the holder exists (ADR-0020): a face leaving the frame mid-visit
    # must not blank the greeting. The machine's copy follows the same rule.
    _recognised_session()
    state.handle_contract_event(
        _vision_event("vision.identity_expired", person_id="prs_alex", reason="expired")
    )
    assert state.session_snapshot_dict()["display_name"] == "Alex"
    row = state.store.load()
    assert row is not None and row.display_name == "Alex"


def test_unenrolling_someone_else_does_not_blank_the_visitor_at_the_door() -> None:
    _recognised_session()
    state.handle_contract_event(
        _vision_event("vision.identity_expired", person_id="prs_someone_else", reason="admin")
    )
    assert state.session_snapshot_dict()["display_name"] == "Alex"
    row = state.store.load()
    assert row is not None and row.person_id == "prs_alex"


def test_privacy_mode_with_nothing_held_changes_nothing() -> None:
    state.machine.handle_button_pressed()
    assert state.machine.forget_identity() is False
    assert state.machine.state == SessionState.VISITOR_MODE


def test_a_restart_forgets_who_was_at_the_door() -> None:
    # ADR-0020: the identity is memory-only. A row written by an older door-api (or by
    # this one, mid-session) still carries the columns; restoring must not read them.
    store = SessionStore(":memory:")
    store.save(
        PersistedSession(
            session_id=uuid4(),
            state=SessionState.VISITOR_MODE,
            trace_id=uuid4(),
            person_id="prs_alex",
            display_name="Alex",
            profile_id="blue_wave",
            started_at_monotonic_ms=1_000,
            last_transition_monotonic_ms=1_000,
            meta_json=json.dumps({"boot_id": "boot"}),
        )
    )
    machine = SessionMachine(
        config=SessionConfig(db_path=":memory:"), store=store, on_event=lambda _e: None
    )
    machine.set_monotonic_fn(lambda: 2_000)
    machine.set_boot_id_fn(lambda: "boot")

    machine.restore_from_persistence()

    assert machine.state == SessionState.VISITOR_MODE, "the session itself survives"
    snapshot = machine.snapshot()
    assert snapshot.person_id is None
    assert snapshot.display_name is None
    assert snapshot.profile_id is None
    # Scrubbed on disk too, so a second restart cannot find it either.
    row = store.load()
    assert row is not None
    assert row.display_name is None and row.person_id is None and row.profile_id is None
