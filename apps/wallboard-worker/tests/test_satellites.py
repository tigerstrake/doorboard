from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from satellites.provider import MockSatelliteProvider, SatelliteConfig, SkyfieldSatelliteProvider
from skyfield.api import load
from wallboard_worker.jobs import run_satellite_passes
from wallboard_worker.settings import Settings

# The bundled de421.bsp lives at the repo root; point the Loader there in tests
# so the real skyfield path never triggers a network download.
REPO_ROOT = Path(__file__).resolve().parents[3]

ISS_TLE = (
    "ISS (ZARYA)\n"
    "1 25544U 98067A   23275.52834241  .00015509  00000-0  27918-3 0  9997\n"
    "2 25544  51.6423 189.6587 0007887  55.5188  41.5288 15.49752831417852"
)


def test_mock_satellite_provider() -> None:
    provider = MockSatelliteProvider()
    res = provider.get_next_pass(datetime.now(UTC))
    assert res is not None
    assert res["satellite"] == "ISS"
    assert res["visible"] is True
    assert res["direction"] == "NW"
    assert res["max_elevation_deg"] == 64.5


@patch("httpx.get")
@patch("skyfield.api.load")
@patch("skyfield.positionlib.Geocentric.is_sunlit")
@patch("skyfield.positionlib.ICRF.altaz")
def test_skyfield_satellite_provider_success(
    mock_altaz, mock_sunlit, mock_load_bsp, mock_httpx_get
) -> None:
    # Set up config with secret coordinates
    config = SatelliteConfig(
        watchlist=["ISS (ZARYA)"],
        observer_lat=37.7749,
        observer_lon=-122.4194,
        observer_elevation=100.0,
        tle_cache_path="/tmp/test_tle_cache.txt",
        # Load the bundled de421.bsp from the repo root instead of downloading.
        ephemeris_dir=str(REPO_ROOT),
    )
    provider = SkyfieldSatelliteProvider(config)

    # 1. Mock CelesTrak TLE get
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ISS_TLE
    mock_httpx_get.return_value = mock_resp

    # 2. Mock BSP load
    mock_eph = MagicMock()
    mock_load_bsp.return_value = mock_eph

    # 3. Mock altaz and sunlit
    mock_sunlit.return_value = True

    # We return two sets of altaz for apparent:
    # First is sun_alt relative to observer (must be < -6 for dark, e.g. -10)
    # Second is sat_alt relative to observer (elevation=45, azimuth=315 -> NW)
    mock_alt_sun = MagicMock()
    mock_alt_sun.degrees = -10.0
    mock_az_sun = MagicMock()
    mock_az_sun.degrees = 180.0

    mock_alt_sat = MagicMock()
    mock_alt_sat.degrees = 45.0
    mock_az_sat = MagicMock()
    mock_az_sat.degrees = 315.0  # NW

    # A callable, not a fixed list: the first altaz call is the sun-darkness check and
    # every later one is the satellite. A list breaks the moment the implementation samples
    # the arc at a different resolution, which is not what this test is about.
    calls = {"n": 0}

    def _altaz(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return (mock_alt_sun, mock_az_sun, None)
        return (mock_alt_sat, mock_az_sat, None)

    mock_altaz.side_effect = _altaz

    # 4. Mock EarthSatellite find_events
    # We patch EarthSatellite find_events to return a single pass (rise, culm, set)
    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
    with patch("skyfield.api.EarthSatellite.find_events") as mock_find:
        ts = load.timescale(builtin=True)
        t_rise = ts.from_datetime(now)
        t_culm = ts.from_datetime(now)
        t_set = ts.from_datetime(now)
        mock_find.return_value = (
            [t_rise, t_culm, t_set],
            [0, 1, 2],  # rise, culmination, set
        )

        res = provider.get_next_pass(now)

    assert res is not None
    assert res["satellite"] == "ISS (ZARYA)"
    assert res["max_elevation_deg"] == 45.0
    assert res["direction"] == "NW"
    assert res["visible"] is True
    # Pass geometry (ADR-0025): the provider already had these events and discarded them.
    assert res["culmination_azimuth_deg"] == 315.0
    assert res["rise_azimuth_deg"] == 315.0
    assert res["set_azimuth_deg"] == 315.0
    assert res["set_at"] is not None
    # rise == set in this fixture, so there is no arc to sample and the track is empty
    # rather than a single degenerate point.
    assert res["track"] == []


@patch("httpx.get")
@patch("skyfield.api.load")
@patch("skyfield.positionlib.Geocentric.is_sunlit")
@patch("skyfield.positionlib.ICRF.altaz")
def test_a_real_pass_carries_a_sampled_arc(
    mock_altaz, mock_sunlit, mock_load_bsp, mock_httpx_get
) -> None:
    """The wallboard draws where to look from this; an empty track means no arc."""
    config = SatelliteConfig(
        watchlist=["ISS (ZARYA)"],
        observer_lat=37.7749,
        observer_lon=-122.4194,
        observer_elevation=100.0,
        tle_cache_path="/tmp/test_tle_cache_arc.txt",
        ephemeris_dir=str(REPO_ROOT),
    )
    provider = SkyfieldSatelliteProvider(config)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ISS_TLE
    mock_resp.raise_for_status = MagicMock()
    mock_httpx_get.return_value = mock_resp
    mock_load_bsp.return_value = MagicMock()
    mock_sunlit.return_value = True

    alt = MagicMock()
    alt.degrees = 30.0
    az = MagicMock()
    az.degrees = 90.0
    dark = MagicMock()
    dark.degrees = -10.0

    calls = {"n": 0}

    def _altaz(*_args, **_kwargs):
        calls["n"] += 1
        return (dark, az, None) if calls["n"] == 1 else (alt, az, None)

    mock_altaz.side_effect = _altaz

    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)
    with patch("skyfield.api.EarthSatellite.find_events") as mock_find:
        ts = load.timescale(builtin=True)
        t_rise = ts.from_datetime(now)
        t_culm = ts.from_datetime(now + timedelta(minutes=3))
        t_set = ts.from_datetime(now + timedelta(minutes=6))
        mock_find.return_value = ([t_rise, t_culm, t_set], [0, 1, 2])

        res = provider.get_next_pass(now)

    assert res is not None
    track = res["track"]
    assert len(track) == provider.TRACK_SAMPLES + 1
    # Monotonic in time, starting at rise and ending at set.
    offsets = [point["t_offset_s"] for point in track]
    assert offsets == sorted(offsets)
    assert offsets[0] == 0.0
    assert offsets[-1] == pytest.approx(360.0, abs=1.0)
    # Never below the horizon: a negative elevation would draw outside the sky dome.
    assert all(point["elevation_deg"] >= 0.0 for point in track)
    assert all(0.0 <= point["azimuth_deg"] < 360.0 for point in track)


def test_ephemeris_uses_loader_pointed_at_configured_writable_dir(tmp_path) -> None:
    # Regression: the module-level skyfield `load` writes de421.bsp into the CWD,
    # which the container worker user can't write ([Errno 13]). The provider must
    # instead use a Loader pointed at the configured, writable ephemeris_dir and
    # create that dir before loading.
    eph_dir = tmp_path / "skyfield_cache"
    config = SatelliteConfig(
        observer_lat=37.7749,
        observer_lon=-122.4194,
        ephemeris_dir=str(eph_dir),
    )
    provider = SkyfieldSatelliteProvider(config)

    sentinel_eph = object()
    with patch("satellites.provider.Loader") as mock_loader_cls:
        # A skyfield Loader instance is callable: loader("de421.bsp").
        mock_loader = MagicMock(return_value=sentinel_eph)
        mock_loader_cls.return_value = mock_loader

        eph = provider._get_ephemeris()

        # Loader constructed pointing at the configured dir; no CWD/global load.
        mock_loader_cls.assert_called_once_with(str(eph_dir))
        mock_loader.assert_called_once_with("de421.bsp")

    assert eph is sentinel_eph
    # The writable dir was created before loading.
    assert eph_dir.is_dir()

    # Second call is cached and does not reconstruct the Loader.
    with patch("satellites.provider.Loader") as mock_loader_again:
        assert provider._get_ephemeris() is sentinel_eph
        mock_loader_again.assert_not_called()


def test_skyfield_satellite_provider_stale_tle_raises_error(tmp_path) -> None:
    cache_file = tmp_path / "tle_cache.txt"
    cache_file.write_text(ISS_TLE, encoding="utf-8")

    # Set file modification time to 8 days ago (stale > 7 days)
    mtime = (datetime.now() - timedelta(days=8)).timestamp()
    os.utime(cache_file, (mtime, mtime))

    config = SatelliteConfig(
        watchlist=["ISS (ZARYA)"],
        observer_lat=37.7749,
        observer_lon=-122.4194,
        tle_cache_path=str(cache_file),
        tle_url="http://invalid.url/tle.txt",
    )
    provider = SkyfieldSatelliteProvider(config)

    # Fetch fails, falls back to cache, but cache is older than 7 days -> raises error
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = Exception("Network offline")
        with pytest.raises(RuntimeError, match="older than 7 days"):
            provider.get_next_pass(datetime.now(UTC))


def test_run_satellite_passes_job_degrades_gracefully() -> None:
    # run_satellite_passes ignores the feature flag (that gate lives in the CLI),
    # so a default Settings is sufficient here.
    settings = Settings()
    mock_provider = MagicMock()
    mock_provider.get_next_pass.side_effect = Exception("Prediction engine failed")

    res = run_satellite_passes(settings, mock_provider)
    assert res is None


@patch("httpx.post")
def test_run_satellite_passes_coordinates_never_leaked(
    mock_post, monkeypatch: pytest.MonkeyPatch
) -> None:
    lat, lon = 37.7749, -122.4194
    monkeypatch.setenv("FEATURE_SATELLITES", "True")
    monkeypatch.setenv("SATELLITES_OBSERVER_LAT", str(lat))
    monkeypatch.setenv("SATELLITES_OBSERVER_LON", str(lon))
    monkeypatch.setenv("CONTROL_PLANE_URL", "http://127.0.0.1:8090")
    monkeypatch.setenv("CONTROL_PLANE_ADMIN_TOKEN", "test-admin")

    settings = Settings()
    provider = MockSatelliteProvider()

    # Mock token generation response and ingest response
    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"token": "tok_ingest_123"}

    ingest_response = MagicMock()
    ingest_response.status_code = 200
    ingest_response.json.return_value = {"status": "stored"}

    mock_post.side_effect = [token_response, ingest_response]

    res = run_satellite_passes(settings, provider)
    assert res is not None

    # Check the body sent to the control plane API /ingest
    ingest_call = mock_post.mock_calls[1]
    body = ingest_call.kwargs["json"]

    # Verify coordinates are absent from event payload
    serialized_payload = json.dumps(body)
    assert str(lat) not in serialized_payload
    assert str(lon) not in serialized_payload


def _ingested_payload(mock_post) -> dict:
    """The payload from the ingest POST, whichever call that turned out to be.

    Indexing `mock_calls[1]` assumes a token fetch happened first, which depends on whether an
    admin token is configured — so it breaks depending on the ambient environment.
    """
    for call in mock_post.mock_calls:
        body = call.kwargs.get("json")
        if isinstance(body, dict) and body.get("events"):
            return body["events"][0]["payload"]
    raise AssertionError("no ingest POST was made")


@patch("httpx.post")
def test_the_job_forwards_the_pass_geometry_it_is_given(mock_post) -> None:
    """The geometry must survive the job, not just exist in the provider.

    This built the payload from five fields while the provider returned set_at, three azimuths
    and a sampled track as well. So every event on the wire carried `track: []`: the sky dome
    rendered its "high point only" fallback in production for as long as it existed, and the
    globe had no trajectory to draw. The fixtures supply a track directly and never went
    through this function, so nothing caught it.
    """
    settings = Settings()

    rise = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
    provider = MagicMock()
    provider.get_next_pass.return_value = {
        "satellite": "ISS (ZARYA)",
        "rise_at": rise,
        "max_elevation_deg": 64.5,
        "direction": "NW",
        "visible": True,
        "set_at": rise + timedelta(seconds=540),
        "rise_azimuth_deg": 315.0,
        "set_azimuth_deg": 135.0,
        "culmination_azimuth_deg": 45.0,
        "track": [
            {
                "t_offset_s": 0.0,
                "azimuth_deg": 315.0,
                "elevation_deg": 0.0,
                "lat": 30.0,
                "lng": -128.0,
            },
            {
                "t_offset_s": 270.0,
                "azimuth_deg": 45.0,
                "elevation_deg": 64.5,
                "lat": 37.6,
                "lng": -122.0,
            },
            {
                "t_offset_s": 540.0,
                "azimuth_deg": 135.0,
                "elevation_deg": 0.0,
                "lat": 45.0,
                "lng": -114.0,
            },
        ],
    }

    token_resp, ingest_resp = MagicMock(), MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {"token": "tok"}
    ingest_resp.status_code = 200
    ingest_resp.json.return_value = {"status": "stored"}
    mock_post.side_effect = [token_resp, ingest_resp]

    assert run_satellite_passes(settings, provider, now=rise) is not None

    payload = _ingested_payload(mock_post)
    assert payload["set_at"] is not None, "the pass had no end on the wire"
    assert payload["rise_azimuth_deg"] == 315.0
    assert payload["culmination_azimuth_deg"] == 45.0
    assert payload["set_azimuth_deg"] == 135.0
    assert len(payload["track"]) == 3, "the trajectory was dropped"
    # And the sub-satellite points the globe plots (ADR-0030).
    assert payload["track"][1]["lat"] == 37.6
    assert payload["track"][1]["lng"] == -122.0


@patch("httpx.post")
def test_a_provider_without_geometry_still_produces_a_valid_event(mock_post) -> None:
    # The mock provider, and any pass found by a path that only knows its culmination, omit
    # these keys entirely. That must stay a payload without geometry, not a KeyError.
    settings = Settings()
    rise = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
    provider = MagicMock()
    provider.get_next_pass.return_value = {
        "satellite": "ISS",
        "rise_at": rise,
        "max_elevation_deg": 40.0,
        "direction": "S",
        "visible": True,
    }

    token_resp, ingest_resp = MagicMock(), MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {"token": "tok"}
    ingest_resp.status_code = 200
    ingest_resp.json.return_value = {"status": "stored"}
    mock_post.side_effect = [token_resp, ingest_resp]

    assert run_satellite_passes(settings, provider, now=rise) is not None
    payload = _ingested_payload(mock_post)
    assert payload["set_at"] is None
    assert payload["track"] == []
