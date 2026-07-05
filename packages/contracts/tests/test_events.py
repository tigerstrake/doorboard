from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from doorboard_contracts import EVENT_TYPE_TO_MODEL, parse_event
from doorboard_contracts.examples import example_events
from pydantic import ValidationError

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tools" / "seed-data" / "events"


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
    data = EVENT_TYPE_TO_MODEL["vision.identity_stable"](
        **example_events()[8].model_dump(mode="json")
    ).model_dump(mode="json")
    payload = cast(dict[str, Any], data["payload"])
    payload["expires_at"] = "2026-07-04T12:34:58.623"

    with pytest.raises(ValidationError):
        parse_event(data)


def test_uuidv7_event_id_is_required() -> None:
    data = example_events()[0].model_dump(mode="json")
    data["event_id"] = "9d1e8a34-52a1-4b0e-9c0f-2a7d1f66b100"

    with pytest.raises(ValidationError):
        parse_event(data)
