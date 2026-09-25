from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from doorboard_contracts import parse_event
from doorboard_contracts.events import AmbientSatellitePassEvent, AmbientSatellitePassPayload
from satellites.provider import MockSatelliteProvider, SatelliteConfig, SkyfieldSatelliteProvider
from skyfield.api import load
from wallboard_worker.jobs import run_satellite_orbits, run_satellite_passes
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


def _pass_payload(pass_data: dict[str, Any]) -> AmbientSatellitePassPayload:
    """Exactly as run_satellite_passes builds it, `.get` for the geometry and all."""
    return AmbientSatellitePassPayload(
        satellite=pass_data["satellite"],
        rise_at=pass_data["rise_at"],
        max_elevation_deg=pass_data["max_elevation_deg"],
        direction=pass_data["direction"],
        visible=pass_data["visible"],
        set_at=pass_data.get("set_at"),
        rise_azimuth_deg=pass_data.get("rise_azimuth_deg"),
        set_azimuth_deg=pass_data.get("set_azimuth_deg"),
        culmination_azimuth_deg=pass_data.get("culmination_azimuth_deg"),
        track=pass_data.get("track") or [],
    )


def test_mock_pass_carries_geometry_that_validates_as_the_contract_payload() -> None:
    """ADR-0025: mock mode has to exercise the panel's real path, not its fallback.

    The mock returned only the five text fields, so under the dev stack the sky dome
    could only ever draw "high point only" and the globe had no arc. The geometry must
    also be a *coherent* pass -- a track that disagrees with its own rise/set/peak would
    draw a confident wrong picture -- and `direction` is the culmination's compass point,
    the way the real provider computes it, not the rise's.
    """
    now = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
    pass_data = MockSatelliteProvider().get_next_pass(now)
    assert pass_data is not None
    payload = _pass_payload(pass_data)

    assert payload.set_at is not None
    assert payload.rise_at > now
    assert payload.set_at > payload.rise_at
    assert payload.track, "no arc to draw"

    first, last = payload.track[0], payload.track[-1]
    assert first.t_offset_s == 0
    assert last.t_offset_s == pytest.approx((payload.set_at - payload.rise_at).total_seconds())
    assert first.elevation_deg == 0 and last.elevation_deg == 0
    assert max(s.elevation_deg for s in payload.track) == pytest.approx(payload.max_elevation_deg)
    assert first.azimuth_deg == payload.rise_azimuth_deg
    assert last.azimuth_deg == payload.set_azimuth_deg
    assert payload.culmination_azimuth_deg is not None
    assert 292.5 <= payload.culmination_azimuth_deg < 337.5, "NW is 292.5-337.5 in the provider"
    assert payload.direction == "NW"
    # The globe needs the ground track too (ADR-0030); a sample without one is the
    # fallback again, one point at a time.
    assert all(s.lat is not None and s.lng is not None for s in payload.track)
    # Deterministic: the dev stack and a test agree on what the mock says.
    assert MockSatelliteProvider().get_next_pass(now) == pass_data


@patch("httpx.post")
def test_run_satellite_passes_puts_the_mock_geometry_on_the_wire(mock_post) -> None:
    """End to end through the job: the event a mock-mode kiosk actually receives has the arc."""
    token_resp, ingest_resp = MagicMock(), MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {"token": "tok"}
    ingest_resp.status_code = 200
    ingest_resp.json.return_value = {"status": "stored"}
    mock_post.side_effect = [token_resp, ingest_resp]

    now = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
    assert run_satellite_passes(Settings(), MockSatelliteProvider(), now=now) is not None

    event = None
    for call in mock_post.mock_calls:
        body = call.kwargs.get("json")
        if isinstance(body, dict) and body.get("events"):
            event = body["events"][0]
            break
    assert event is not None, "no ingest POST was made"
    parsed = parse_event(event)
    assert isinstance(parsed, AmbientSatellitePassEvent)
    assert parsed.payload.set_at is not None
    assert len(parsed.payload.track) > 2
    assert parsed.payload.rise_azimuth_deg is not None


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
    # The file has to be there: the provider refuses to let the Loader download it.
    eph_dir.mkdir()
    (eph_dir / "de421.bsp").write_bytes(b"")
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
    assert eph_dir.is_dir()

    # Second call is cached and does not reconstruct the Loader.
    with patch("satellites.provider.Loader") as mock_loader_again:
        assert provider._get_ephemeris() is sentinel_eph
        mock_loader_again.assert_not_called()


def test_a_missing_ephemeris_fails_fast_instead_of_downloading(tmp_path) -> None:
    """skyfield's Loader fetches a missing de421.bsp from NAIF: ~17 MB, no timeout.

    SATELLITES_EPHEMERIS_DIR is operator-overridable, so a dir mounted empty was enough
    to trigger it -- on the scheduler's loop thread, stalling every other tile. The
    image bundles the file; the worker must never fetch it.
    """
    config = SatelliteConfig(
        observer_lat=37.7749,
        observer_lon=-122.4194,
        ephemeris_dir=str(tmp_path / "empty"),
    )
    provider = SkyfieldSatelliteProvider(config)

    with patch("satellites.provider.Loader") as mock_loader_cls:
        with pytest.raises(RuntimeError, match="de421.bsp is missing"):
            provider._get_ephemeris()
        mock_loader_cls.assert_not_called()


def _tle_provider(cache_file: Path) -> SkyfieldSatelliteProvider:
    return SkyfieldSatelliteProvider(
        SatelliteConfig(
            observer_lat=37.7749,
            observer_lon=-122.4194,
            tle_cache_path=str(cache_file),
            tle_url="http://example.test/tle.txt",
        )
    )


def _age(path: Path, *, days: float) -> None:
    mtime = (datetime.now() - timedelta(days=days)).timestamp()
    os.utime(path, (mtime, mtime))


@pytest.mark.parametrize(
    "body", ["No GP data found", "<html><body><h1>Down for maintenance</h1></body></html>"]
)
def test_an_empty_200_does_not_poison_a_good_tle_cache(tmp_path, body: str) -> None:
    """CelesTrak answers HTTP 200 with a plain-text "No GP data found" or an HTML
    maintenance page. That body used to overwrite the cache before anything looked at
    it, parse to zero satellites, and the fresh-cache rule then served it for 24 h:
    a blank tile for a day, with nothing at error level.
    """
    cache_file = tmp_path / "tle.txt"
    cache_file.write_text(ISS_TLE, encoding="utf-8")
    _age(cache_file, days=2)  # stale enough to refetch, young enough to fall back on
    provider = _tle_provider(cache_file)

    empty = MagicMock()
    empty.status_code = 200
    empty.text = body
    with patch("httpx.get", return_value=empty):
        tles = provider._get_tles()

    assert "ISS (ZARYA)" in tles, "the prior good cache was not served"
    assert cache_file.read_text(encoding="utf-8") == ISS_TLE, "the empty body overwrote it"


def test_a_fresh_cache_holding_no_tles_is_refetched(tmp_path) -> None:
    # A cache poisoned by a worker that trusted any 200 must not be honoured for the
    # rest of its 24 h just because it is young.
    cache_file = tmp_path / "tle.txt"
    cache_file.write_text("No GP data found", encoding="utf-8")
    provider = _tle_provider(cache_file)

    good = MagicMock()
    good.status_code = 200
    good.text = ISS_TLE
    with patch("httpx.get", return_value=good) as get:
        tles = provider._get_tles()

    get.assert_called_once()
    assert "ISS (ZARYA)" in tles
    assert cache_file.read_text(encoding="utf-8") == ISS_TLE


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


ORBIT_NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=UTC)


def test_mock_provider_produces_full_orbits() -> None:
    """Mock mode must draw whole orbits offline (ADR-0041, hardware-optional rule)."""
    orbits = MockSatelliteProvider().get_orbits(ORBIT_NOW)

    names = {orbit["name"] for orbit in orbits}
    assert "ISS (ZARYA)" in names
    assert "HST" in names
    assert len(orbits) == 4

    for orbit in orbits:
        track = orbit["track"]
        assert len(track) >= 30
        # The current sub-point is the first sample (now).
        assert orbit["sub_lat"] == track[0]["lat"]
        assert orbit["sub_lng"] == track[0]["lng"]
        # Absolute, non-decreasing sample times that start at `now`.
        times = [sample["at"] for sample in track]
        assert times == sorted(times)
        assert times[0] == ORBIT_NOW
        # A whole revolution wraps the globe, and latitude stays in the inclination envelope.
        lngs = [sample["lng"] for sample in track]
        assert max(lngs) - min(lngs) > 180
        assert all(-99.0 <= sample["lat"] <= 99.0 for sample in track)


def test_mock_orbits_are_deterministic() -> None:
    # A fixed `now` must give a fixed result, so tests and CI are stable.
    assert MockSatelliteProvider().get_orbits(ORBIT_NOW) == MockSatelliteProvider().get_orbits(
        ORBIT_NOW
    )


@patch("httpx.get")
def test_skyfield_get_orbits_samples_a_full_period(mock_httpx_get, tmp_path) -> None:
    """A real ISS TLE, propagated offline — sub-satellite points need no ephemeris."""
    config = SatelliteConfig(
        observer_lat=37.7749,
        observer_lon=-122.4194,
        orbit_norad_ids=[25544],
        orbit_tle_url="http://example.test/orbit.txt",
        orbit_tle_cache_path=str(tmp_path / "orbit.txt"),
        orbit_samples=16,
    )
    provider = SkyfieldSatelliteProvider(config)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ISS_TLE
    mock_httpx_get.return_value = mock_resp

    orbits = provider.get_orbits(ORBIT_NOW)

    assert len(orbits) == 1
    orbit = orbits[0]
    assert orbit["norad_id"] == 25544
    track = orbit["track"]
    assert len(track) == 17  # orbit_samples + 1
    assert orbit["sub_lat"] == track[0]["lat"]
    times = [sample["at"] for sample in track]
    assert times == sorted(times)
    assert times[0] == ORBIT_NOW
    # One ISS revolution is ~92-93 minutes.
    span_min = (times[-1] - times[0]).total_seconds() / 60.0
    assert 88.0 <= span_min <= 96.0
    # ISS inclination ~51.6°, so the ground track stays within ~±52°, and wraps the planet.
    assert all(-53.0 <= sample["lat"] <= 53.0 for sample in track)
    lngs = [sample["lng"] for sample in track]
    assert max(lngs) - min(lngs) > 180.0


@patch("httpx.get")
def test_skyfield_get_orbits_skips_a_norad_not_in_the_feed(mock_httpx_get, tmp_path) -> None:
    # An id absent from the fetched TLE set is skipped, not fatal.
    config = SatelliteConfig(
        observer_lat=37.7749,
        observer_lon=-122.4194,
        orbit_norad_ids=[99999],
        orbit_tle_url="http://example.test/orbit.txt",
        orbit_tle_cache_path=str(tmp_path / "orbit_skip.txt"),
        orbit_samples=8,
    )
    provider = SkyfieldSatelliteProvider(config)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = ISS_TLE
    mock_httpx_get.return_value = mock_resp

    assert provider.get_orbits(ORBIT_NOW) == []


def test_run_satellite_orbits_degrades_gracefully() -> None:
    provider = MagicMock()
    provider.get_orbits.side_effect = Exception("propagation failed")
    assert run_satellite_orbits(Settings(), provider) is None


@patch("httpx.post")
def test_run_satellite_orbits_emits_a_valid_event(mock_post) -> None:
    """The job forwards the tracked set as a contract-valid ambient.satellite_orbits event."""
    token_resp, ingest_resp = MagicMock(), MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {"token": "tok"}
    ingest_resp.status_code = 200
    ingest_resp.json.return_value = {"status": "stored"}
    mock_post.side_effect = [token_resp, ingest_resp]

    assert run_satellite_orbits(Settings(), MockSatelliteProvider(), now=ORBIT_NOW) is not None

    # Whichever POST carried the batch, parse the event back through the contract.
    event = None
    for call in mock_post.mock_calls:
        body = call.kwargs.get("json")
        if isinstance(body, dict) and body.get("events"):
            event = body["events"][0]
            break
    assert event is not None, "no ingest POST was made"

    parsed = parse_event(event)
    assert parsed.type == "ambient.satellite_orbits"
    assert len(parsed.payload.satellites) == 4
    first = parsed.payload.satellites[0]
    assert first.norad_id == 25544
    assert len(first.track) >= 30
    assert first.sub_lat == first.track[0].lat


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
