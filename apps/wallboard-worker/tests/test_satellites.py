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

    mock_altaz.side_effect = [
        (mock_alt_sun, mock_az_sun, None),  # Sun check
        (mock_alt_sat, mock_az_sat, None),  # Sat check
    ]

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
