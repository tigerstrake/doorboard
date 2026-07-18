"""Unit tests for system.latency_sample event generation."""

from __future__ import annotations

from doorboard_contracts import EVENT_ADAPTER
from doorboard_observability.metrics import (
    drain_latency_events,
    get_samples,
    latency_sample_payload,
    record_sample,
    reset_samples,
)


def test_latency_sample_window_is_bounded() -> None:
    reset_samples()
    for i in range(4_097):
        record_sample("button_to_generic_feedback", float(i))

    samples = get_samples("button_to_generic_feedback")
    assert len(samples) == 4_096
    assert samples[0] == 1.0
    assert samples[-1] == 4_096.0


def test_latency_sample_payload_empty() -> None:
    reset_samples()
    payload = latency_sample_payload("button_to_generic_feedback", 60)
    assert payload.path == "button_to_generic_feedback"
    assert payload.p50_ms == 0.0
    assert payload.p95_ms == 0.0
    assert payload.p99_ms == 0.0
    assert payload.window_s == 60


def test_latency_sample_payload_with_data() -> None:
    reset_samples()
    for i in range(1, 101):
        record_sample("button_to_generic_feedback", float(i))

    payload = latency_sample_payload("button_to_generic_feedback", 60)
    assert payload.path == "button_to_generic_feedback"
    assert payload.p50_ms == 50.0
    assert payload.p95_ms == 95.0
    assert payload.p99_ms == 99.0
    assert payload.window_s == 60


def test_drain_latency_events_validates() -> None:
    reset_samples()
    # Record samples for two different paths
    for _ in range(10):
        record_sample("button_to_generic_feedback", 5.0)
    for _ in range(10):
        record_sample("tap_to_local_response", 10.0)

    events = drain_latency_events(source="test_runner", door_id="door-123", window_s=60)

    # We should have one event per path
    assert len(events) == 2
    paths = {e.payload.path for e in events}  # type: ignore
    assert paths == {"button_to_generic_feedback", "tap_to_local_response"}

    for event in events:
        # Validate against the contract TypeAdapter
        # Dump to JSON then load to simulate the wire
        raw_json = event.model_dump_json()
        validated = EVENT_ADAPTER.validate_json(raw_json)

        assert validated.type == "system.latency_sample"
        assert validated.source == "test_runner"
        assert validated.door_id == "door-123"
        assert validated.payload.window_s == 60  # type: ignore

    # Ensure the window was cleared
    events_after = drain_latency_events(source="test_runner", door_id="door-123", window_s=60)
    assert len(events_after) == 0


def test_drain_latency_events_uuid7() -> None:
    reset_samples()
    record_sample("button_to_generic_feedback", 5.0)
    events = drain_latency_events("src", "door", 60)
    assert len(events) == 1
    # UUIDv7 starts with timestamp in MSB, version in 13th nibble (index 14 in hex)
    assert events[0].event_id.version == 7
