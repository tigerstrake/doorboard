from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast, get_args

import pytest
from doorboard_contracts import (
    EVENT_TYPE_TO_MODEL,
    LEGAL_SESSION_TRANSITIONS,
    SessionState,
    parse_event,
)
from doorboard_contracts.events import SessionEndedPayload
from doorboard_contracts.examples import example_events
from pydantic import ValidationError

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tools" / "seed-data" / "events"
IN_SESSION_STATES = {
    SessionState.BUTTON_PRESSED,
    SessionState.VISITOR_MODE,
    SessionState.RINGING,
    SessionState.ANSWERED,
    SessionState.UNANSWERED_TIMEOUT,
    SessionState.VIDEO_MESSAGE_OFFERED,
    SessionState.VIDEO_MESSAGE_RECORDING,
    SessionState.VIDEO_MESSAGE_REVIEW,
}


def _reachable(start: SessionState) -> set[SessionState]:
    visited: set[SessionState] = set()
    frontier = [start]
    while frontier:
        state = frontier.pop()
        for next_state in LEGAL_SESSION_TRANSITIONS[state]:
            if next_state not in visited:
                visited.add(next_state)
                frontier.append(next_state)
    return visited


def test_every_catalog_event_round_trips() -> None:
    events = example_events()

    assert {event.type for event in events} == set(EVENT_TYPE_TO_MODEL)

    for event in events:
        serialized = event.model_dump(mode="json")
        parsed = parse_event(serialized)

        assert parsed == event


def test_fixture_files_cover_every_event_type() -> None:
    fixture_types: set[str] = set()
    for fixture_path in FIXTURE_DIR.glob("*.json"):
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixture_types.add(data["type"])
        assert parse_event(data).model_dump(mode="json") == data

    assert fixture_types == set(EVENT_TYPE_TO_MODEL)


def test_extra_envelope_field_is_rejected() -> None:
    data = example_events()[0].model_dump(mode="json")
    data["unexpected"] = True

    with pytest.raises(ValidationError):
        parse_event(data)


def test_extra_payload_field_is_rejected() -> None:
    data = example_events()[0].model_dump(mode="json")
    payload = cast(dict[str, Any], data["payload"])
    payload["unexpected"] = True

    with pytest.raises(ValidationError):
        parse_event(data)


def test_wrong_payload_field_is_rejected() -> None:
    data = example_events()[0].model_dump(mode="json")
    payload = cast(dict[str, Any], data["payload"])
    payload["had_cached_profiles"] = payload.pop("had_cached_profile")

    with pytest.raises(ValidationError):
        parse_event(data)


@pytest.mark.parametrize("field", ["occurred_at"])
def test_naive_envelope_datetime_is_rejected(field: str) -> None:
    data: dict[str, Any] = example_events()[0].model_dump(mode="json")
    data[field] = "2026-07-04T12:34:56.123"

    with pytest.raises(ValidationError):
        parse_event(data)


def test_naive_payload_datetime_is_rejected() -> None:
    identity_event = next(
        event for event in example_events() if event.type == "vision.identity_stable"
    )
    data = identity_event.model_dump(mode="json")
    payload = cast(dict[str, Any], data["payload"])
    payload["expires_at"] = "2026-07-04T12:34:58.623"

    with pytest.raises(ValidationError):
        parse_event(data)


def test_every_session_ended_outcome_is_reachable() -> None:
    outcome_states = {
        "answered": SessionState.ANSWERED,
        "unanswered_timeout": SessionState.UNANSWERED_TIMEOUT,
        "message_left": SessionState.VIDEO_MESSAGE_SAVED,
        "abandoned": SessionState.BUTTON_PRESSED,
        "reset": SessionState.VISITOR_MODE,
    }
    outcome_annotation = SessionEndedPayload.model_fields["outcome"].annotation

    assert set(get_args(outcome_annotation)) == set(outcome_states)

    reachable_from_idle = _reachable(SessionState.IDLE)
    for state in outcome_states.values():
        assert state in reachable_from_idle
        assert SessionState.SESSION_END in _reachable(state)


def test_session_end_is_reachable_from_every_in_session_state() -> None:
    for state in IN_SESSION_STATES:
        assert SessionState.SESSION_END in _reachable(state)


def test_uuidv7_event_id_is_required() -> None:
    data = example_events()[0].model_dump(mode="json")
    data["event_id"] = "9d1e8a34-52a1-4b0e-9c0f-2a7d1f66b100"

    with pytest.raises(ValidationError):
        parse_event(data)
