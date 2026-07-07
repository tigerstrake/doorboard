"""Pure precedence/expiry resolver + webhook coordinate rejection + staleness (T-504).

No DB, no HTTP — this is the part reviewers most need to trust, and the
brief's acceptance criterion is explicit: "Table-driven tests over all
source-combination x expiry cases; coordinate-bearing payload rejected with
test coverage."
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from control_plane_api.presence import (
    CoordinatePayloadError,
    MockCalendarProvider,
    SourceEntry,
    is_stale,
    reject_coordinate_payload,
    resolve_presence,
)
from doorboard_contracts import PresenceLabel

NOW = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
PAST = NOW - timedelta(hours=1)
FUTURE = NOW + timedelta(hours=1)


@pytest.mark.parametrize(
    ("entries", "expected_label", "expected_source"),
    [
        pytest.param(
            {"manual": SourceEntry(PresenceLabel.BUSY)},
            PresenceLabel.BUSY,
            "manual",
            id="manual only",
        ),
        pytest.param(
            {"focus_shortcut": SourceEntry(PresenceLabel.AVAILABLE)},
            PresenceLabel.AVAILABLE,
            "focus_shortcut",
            id="focus_shortcut only",
        ),
        pytest.param(
            {"geofence_label": SourceEntry(PresenceLabel.AT_LIBRARY)},
            PresenceLabel.AT_LIBRARY,
            "geofence_label",
            id="geofence_label only",
        ),
        pytest.param(
            {"calendar": SourceEntry(PresenceLabel.AT_CLASS)},
            PresenceLabel.AT_CLASS,
            "calendar",
            id="calendar only",
        ),
        pytest.param(
            {"default": SourceEntry(PresenceLabel.AWAY)},
            PresenceLabel.AWAY,
            "default",
            id="default only",
        ),
        pytest.param(
            {}, PresenceLabel.UNKNOWN, "default", id="nothing at all -> hardcoded unknown"
        ),
        pytest.param(
            {
                "manual": SourceEntry(PresenceLabel.DO_NOT_DISTURB),
                "focus_shortcut": SourceEntry(PresenceLabel.AVAILABLE),
                "geofence_label": SourceEntry(PresenceLabel.AT_LIBRARY),
                "calendar": SourceEntry(PresenceLabel.AT_CLASS),
                "default": SourceEntry(PresenceLabel.AWAY),
            },
            PresenceLabel.DO_NOT_DISTURB,
            "manual",
            id="manual beats everything",
        ),
        pytest.param(
            {
                "focus_shortcut": SourceEntry(PresenceLabel.SLEEPING),
                "geofence_label": SourceEntry(PresenceLabel.AT_LIBRARY),
                "calendar": SourceEntry(PresenceLabel.AT_CLASS),
                "default": SourceEntry(PresenceLabel.AWAY),
            },
            PresenceLabel.SLEEPING,
            "focus_shortcut",
            id="focus_shortcut beats geofence/calendar/default",
        ),
        pytest.param(
            {
                "geofence_label": SourceEntry(PresenceLabel.AT_LIBRARY),
                "calendar": SourceEntry(PresenceLabel.AT_CLASS),
                "default": SourceEntry(PresenceLabel.AWAY),
            },
            PresenceLabel.AT_LIBRARY,
            "geofence_label",
            id="geofence_label beats calendar/default",
        ),
        pytest.param(
            {
                "calendar": SourceEntry(PresenceLabel.AT_CLASS),
                "default": SourceEntry(PresenceLabel.AWAY),
            },
            PresenceLabel.AT_CLASS,
            "calendar",
            id="calendar beats default",
        ),
        pytest.param(
            {
                "manual": SourceEntry(PresenceLabel.BUSY, until=PAST),
                "focus_shortcut": SourceEntry(PresenceLabel.AVAILABLE),
            },
            PresenceLabel.AVAILABLE,
            "focus_shortcut",
            id="expired manual falls through to focus_shortcut",
        ),
        pytest.param(
            {
                "manual": SourceEntry(PresenceLabel.BUSY, until=PAST),
                "focus_shortcut": SourceEntry(PresenceLabel.SLEEPING, until=PAST),
                "geofence_label": SourceEntry(PresenceLabel.AT_LIBRARY),
            },
            PresenceLabel.AT_LIBRARY,
            "geofence_label",
            id="expired manual+focus_shortcut fall through to geofence_label",
        ),
        pytest.param(
            {
                "manual": SourceEntry(PresenceLabel.BUSY, until=PAST),
                "focus_shortcut": SourceEntry(PresenceLabel.SLEEPING, until=PAST),
                "geofence_label": SourceEntry(PresenceLabel.AT_LIBRARY, until=PAST),
                "calendar": SourceEntry(PresenceLabel.AT_CLASS, until=PAST),
            },
            PresenceLabel.UNKNOWN,
            "default",
            id="everything expired falls through to hardcoded default",
        ),
        pytest.param(
            {
                "manual": SourceEntry(PresenceLabel.BUSY, until=FUTURE),
                "focus_shortcut": SourceEntry(PresenceLabel.AVAILABLE),
            },
            PresenceLabel.BUSY,
            "manual",
            id="not-yet-expired manual (future until) still wins",
        ),
        pytest.param(
            {
                "manual": SourceEntry(PresenceLabel.BUSY, until=NOW),
                "focus_shortcut": SourceEntry(PresenceLabel.AVAILABLE),
            },
            PresenceLabel.AVAILABLE,
            "focus_shortcut",
            id="until exactly now counts as expired",
        ),
        pytest.param(
            {"default": SourceEntry(PresenceLabel.AWAY, until=PAST)},
            PresenceLabel.UNKNOWN,
            "default",
            id="expired default still falls to hardcoded unknown",
        ),
        pytest.param(
            {
                "manual": SourceEntry(PresenceLabel.BUSY, until=PAST),
                "calendar": SourceEntry(PresenceLabel.AT_CLASS),
                "default": SourceEntry(PresenceLabel.AWAY),
            },
            PresenceLabel.AT_CLASS,
            "calendar",
            id="expired manual skips missing focus_shortcut/geofence_label straight to calendar",
        ),
    ],
)
def test_resolve_presence_table(
    entries: dict[str, SourceEntry], expected_label: PresenceLabel, expected_source: str
) -> None:
    resolved = resolve_presence(entries, now=NOW)
    assert resolved.label == expected_label
    assert resolved.source == expected_source


def test_resolve_presence_carries_the_winning_sources_until() -> None:
    resolved = resolve_presence({"manual": SourceEntry(PresenceLabel.BUSY, until=FUTURE)}, now=NOW)
    assert resolved.until == FUTURE


def test_resolve_presence_default_fallback_has_no_until() -> None:
    resolved = resolve_presence({}, now=NOW)
    assert resolved.until is None


# ---------------------------------------------------------------------------
# Calendar provider stub (real wiring is a later brief)
# ---------------------------------------------------------------------------


def test_mock_calendar_provider_returns_none_until_canned() -> None:
    provider = MockCalendarProvider()
    assert provider.get_label("owner", now=NOW) is None

    entry = SourceEntry(PresenceLabel.AT_CLASS, until=FUTURE)
    provider.set_canned("owner", entry)
    assert provider.get_label("owner", now=NOW) == entry
    assert provider.get_label("roommate", now=NOW) is None  # per-subject, not global

    provider.set_canned("owner", None)
    assert provider.get_label("owner", now=NOW) is None


# ---------------------------------------------------------------------------
# Coordinate rejection (ARCHITECTURE.md §9: "no raw GPS anywhere")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {"subject_id": "owner", "label": "available", "latitude": 37.4, "longitude": -122.1},
            id="latitude/longitude",
        ),
        pytest.param(
            {"subject_id": "owner", "label": "available", "lat": 37.4, "lon": -122.1},
            id="lat/lon",
        ),
        pytest.param(
            {"subject_id": "owner", "label": "available", "gps": {"lat": 1, "lon": 2}},
            id="nested gps object",
        ),
        pytest.param(
            {"subject_id": "owner", "label": "available", "location": {"coordinates": [1, 2]}},
            id="nested location/coordinates",
        ),
        pytest.param(
            {"subject_id": "owner", "label": "available", "extra": [{"geo": "x"}]},
            id="coordinate key inside a list",
        ),
    ],
)
def test_reject_coordinate_payload_raises(payload: dict) -> None:
    with pytest.raises(CoordinatePayloadError):
        reject_coordinate_payload(payload, context="test")


def test_reject_coordinate_payload_logs_the_offending_field(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        caplog.at_level("WARNING", logger="control_plane_api.presence"),
        pytest.raises(CoordinatePayloadError),
    ):
        reject_coordinate_payload(
            {"subject_id": "owner", "lat": 1}, context="webhook:focus_shortcut"
        )
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.message == "presence_webhook_coordinate_payload_rejected"
    assert record.context == "webhook:focus_shortcut"  # type: ignore[attr-defined]
    assert record.fields == ["lat"]  # type: ignore[attr-defined]


def test_reject_coordinate_payload_allows_clean_payload() -> None:
    reject_coordinate_payload(
        {"subject_id": "owner", "label": "available", "until": None}, context="test"
    )  # must not raise


# ---------------------------------------------------------------------------
# Staleness (NUC-outage drill helper)
# ---------------------------------------------------------------------------


def test_is_stale_false_within_threshold() -> None:
    assert is_stale(NOW, now=NOW, max_age_s=60) is False
    assert is_stale(NOW, now=NOW + timedelta(seconds=59), max_age_s=60) is False


def test_is_stale_true_past_threshold() -> None:
    assert is_stale(NOW, now=NOW + timedelta(seconds=61), max_age_s=60) is True
