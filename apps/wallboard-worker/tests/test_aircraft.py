from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from aircraft.provider import (
    AircraftConfig,
    MockAircraftProvider,
    OpenSkyAircraftProvider,
    haversine_distance,
)
from wallboard_worker.jobs import run_aircraft_summary
from wallboard_worker.settings import Settings

OPENSKY_MOCK_RESPONSE = {
    "time": 1696250000,
    "states": [
        # 1: callsign, 5: longitude, 6: latitude, 7: baro_alt, 8: on_ground, 10: true_track
        [
            "4b1812",
            "SWR123  ",
            "Switzerland",
            1696250000,
            1696250000,
            -122.3,
            37.8,
            3048.0,  # 3048 meters ~ 10000 feet
            False,
            200.0,
            90.0,
            0.0,
            None,
            3048.0,
            "1000",
            False,
            0,
        ],
        # On ground (should be filtered out)
        [
            "4b1813",
            "UAL456  ",
            "United States",
            1696250000,
            1696250000,
            -122.41,
            37.77,
            0.0,
            True,  # on ground
            0.0,
            180.0,
            0.0,
            None,
            0.0,
            "2000",
            False,
            0,
        ],
    ],
}


def test_mock_aircraft_provider() -> None:
    provider = MockAircraftProvider()
    res = provider.get_nearby_aircraft(datetime.now(UTC))
    assert len(res) == 2
    assert res[0]["callsign"] == "UAL123"
    assert res[0]["altitude_ft"] == 12500
    assert res[0]["distance_km"] == 15.42
    assert res[0]["heading"] == 180


def test_haversine_distance() -> None:
    # SFO to Oakland Airport distance (~18 km)
    dist = haversine_distance(37.6213, -122.3790, 37.7213, -122.2207)
    assert 15.0 < dist < 22.0


@patch("httpx.get")
def test_opensky_provider_success(mock_get) -> None:
    config = AircraftConfig(
        observer_lat=37.7749,
        observer_lon=-122.4194,
        bbox_half_size_lat=0.1,
        bbox_half_size_lon=0.1,
        poll_cooldown_seconds=10,
    )
    provider = OpenSkyAircraftProvider(config)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = OPENSKY_MOCK_RESPONSE
    mock_get.return_value = mock_resp

    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
    res = provider.get_nearby_aircraft(now)

    # 1. Check HTTP call parameters
    mock_get.assert_called_once()
    called_params = mock_get.call_args[1]["params"]
    assert called_params["lamin"] == pytest.approx(37.6749)
    assert called_params["lamax"] == pytest.approx(37.8749)
    assert called_params["lomin"] == pytest.approx(-122.5194)
    assert called_params["lomax"] == pytest.approx(-122.3194)

    # 2. Check filtered and parsed results
    assert len(res) == 1
    ac = res[0]
    assert ac["callsign"] == "SWR123"
    assert ac["altitude_ft"] == pytest.approx(10000, abs=10)
    assert ac["heading"] == 90
    assert ac["distance_km"] > 0


@patch("httpx.get")
def test_opensky_provider_cooldown_and_cache(mock_get) -> None:
    config = AircraftConfig(
        observer_lat=37.7749,
        observer_lon=-122.4194,
        poll_cooldown_seconds=10,
    )
    provider = OpenSkyAircraftProvider(config)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = OPENSKY_MOCK_RESPONSE
    mock_get.return_value = mock_resp

    # First call - hits API
    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
    res1 = provider.get_nearby_aircraft(now)
    assert len(res1) == 1
    assert mock_get.call_count == 1

    # Second call (after 5 seconds) - within cooldown, should use cache and NOT hit API
    res2 = provider.get_nearby_aircraft(now + timedelta(seconds=5))
    assert len(res2) == 1
    assert mock_get.call_count == 1  # Still 1 call

    # Third call (after 11 seconds) - cooldown expired, should hit API again
    res3 = provider.get_nearby_aircraft(now + timedelta(seconds=11))
    assert len(res3) == 1
    assert mock_get.call_count == 2  # Incremented to 2


@patch("httpx.get")
def test_opensky_provider_outage_fallback(mock_get) -> None:
    config = AircraftConfig(
        observer_lat=37.7749,
        observer_lon=-122.4194,
        poll_cooldown_seconds=10,
    )
    provider = OpenSkyAircraftProvider(config)

    # Prime cache first
    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.json.return_value = OPENSKY_MOCK_RESPONSE
    mock_get.return_value = mock_resp_ok

    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
    provider.get_nearby_aircraft(now)
    assert len(provider._cached_aircraft) == 1

    # Simulate API failure (e.g. rate limit 429 or server error 500)
    mock_resp_fail = MagicMock()
    mock_resp_fail.status_code = 429
    mock_get.return_value = mock_resp_fail

    # Call again after cooldown expired to force API call
    res = provider.get_nearby_aircraft(now + timedelta(seconds=12))
    # Should fall back to cached data without crashing
    assert len(res) == 1
    assert res[0]["callsign"] == "SWR123"


@patch("httpx.post")
def test_run_aircraft_summary_fields_verification(
    mock_post, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FEATURE_AIRCRAFT", "True")
    monkeypatch.setenv("CONTROL_PLANE_URL", "http://127.0.0.1:8090")
    monkeypatch.setenv("CONTROL_PLANE_ADMIN_TOKEN", "test-admin")

    settings = Settings()
    provider = MockAircraftProvider()

    # Mock token generation response and ingest response
    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"token": "tok_ingest_123"}

    ingest_response = MagicMock()
    ingest_response.status_code = 200
    ingest_response.json.return_value = {"status": "stored"}

    mock_post.side_effect = [token_response, ingest_response]

    res = run_aircraft_summary(settings, provider)
    assert res is not None

    # Verify that only the specified fields are present in the JSON body
    ingest_call = mock_post.mock_calls[1]
    body = ingest_call.kwargs["json"]

    batch_events = body["events"]
    assert len(batch_events) == 1
    event = batch_events[0]
    assert event["type"] == "ambient.aircraft_summary"

    payload = event["payload"]
    assert "nearby" in payload
    assert len(payload["nearby"]) == 2

    # Check for fields
    for ac in payload["nearby"]:
        # Only these 4 fields are allowed
        assert set(ac.keys()) == {"callsign", "altitude_ft", "distance_km", "heading"}
        # No fabricated route or destination info
        assert "route" not in ac
        assert "origin" not in ac
        assert "destination" not in ac
