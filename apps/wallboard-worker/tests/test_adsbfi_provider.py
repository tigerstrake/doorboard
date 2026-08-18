"""The keyless aircraft feed, and why it exists.

OpenSky's anonymous tier cannot do this job. On the owner's door it answered HTTP 429 to every
poll, and the API itself reported `x-rate-limit-retry-after-seconds: 16841` — nearly five
hours. No polling cadence fixes that, so an unconfigured door needs a feed that will answer.
adsb.fi needs no key and, at the same moment OpenSky was refusing, returned 26 aircraft within
50 nm of the door.

Field shapes below are copied from a real response.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from aircraft.provider import (
    AdsbFiAircraftProvider,
    AircraftConfig,
    AircraftDataUnavailable,
)

NOW = datetime(2026, 8, 18, 10, 0, 0, tzinfo=UTC)

# Straight from opendata.adsb.fi, trimmed to the fields the mapper reads.
REAL_RESPONSE = {
    "resultCount": 5,
    "aircraft": [
        {
            "hex": "a0ec20",
            "flight": "FDX1865 ",
            "lat": 37.477936,
            "lon": -122.027309,
            "alt_baro": 11300,
            "track": 138.64,
            "gs": 345.1,
            "baro_rate": 832,
            "r": "N159FE",
            "t": "B763",
        },
        # On the ground at a nearby airport: nearest by distance, and not what "overhead
        # aircraft" means. Must be dropped, or the tile leads with parked jets.
        {
            "hex": "aaa111",
            "flight": "PARKED1 ",
            "lat": 37.4600,
            "lon": -122.1150,
            "alt_baro": "ground",
            "track": 0,
            "gs": 0,
        },
        # No position: unplaceable, so unusable.
        {"hex": "bbb222", "flight": "NOPOS  ", "alt_baro": 30000, "track": 90},
        # On the ground but reporting a *numeric* altitude — near sea level it can read
        # negative. Seen live: an Asiana 747 at "-75 ft" listed among the overhead aircraft.
        {
            "hex": "ccc333",
            "flight": "AIH283 ",
            "lat": 37.6188,
            "lon": -122.3750,
            "alt_baro": -75,
            "track": 297,
            "gs": 12.0,
        },
        {
            "hex": "a24a82",
            "flight": "FDX1885 ",
            "lat": 37.809215,
            "lon": -122.368887,
            "alt_baro": 5725,
            "track": 338.67,
            "gs": 269.5,
            "baro_rate": 3008,
            "r": "N247FE",
            "t": "B762",
        },
    ],
}


def _provider(handler, **overrides) -> AdsbFiAircraftProvider:
    config = AircraftConfig(
        observer_lat=37.422, observer_lon=-122.172, poll_cooldown_seconds=30, **overrides
    )
    provider = AdsbFiAircraftProvider(config)
    provider._transport = httpx.MockTransport(handler)  # type: ignore[attr-defined]
    return provider


def test_it_maps_a_real_response_onto_the_summary_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.get", lambda *a, **k: httpx.Response(200, json=REAL_RESPONSE))
    provider = AdsbFiAircraftProvider(AircraftConfig(observer_lat=37.422, observer_lon=-122.172))
    result = provider.get_nearby_aircraft(NOW)

    # Two of the five are usable: one is flagged on the ground, one has no position, and one
    # reports a negative barometric altitude while parked.
    assert len(result) == 2
    nearest = result[0]
    assert nearest["callsign"] == "FDX1865", "the trailing pad must be stripped"
    assert nearest["altitude_ft"] == 11300
    assert nearest["icao24"] == "a0ec20"
    assert nearest["heading"] == 138
    # Knots on the wire, km/h in the contract — and an int, not a float. A float raised
    # int_from_float inside AmbientAircraftNearby and took the whole summary job down.
    assert nearest["ground_speed_kmh"] == round(345.1 * 1.852)
    assert isinstance(nearest["ground_speed_kmh"], int)
    assert nearest["vertical_rate_fpm"] == 832
    # Registration and type arrive inline, where OpenSky needs two more services.
    assert nearest["registration"] == "N159FE"
    assert nearest["aircraft_type"] == "B763"
    assert nearest["on_ground"] is False
    # Sorted by distance, and the far one really is farther.
    assert result[0]["distance_km"] < result[1]["distance_km"]


def test_grounded_and_unplaceable_aircraft_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx.get", lambda *a, **k: httpx.Response(200, json=REAL_RESPONSE))
    provider = AdsbFiAircraftProvider(AircraftConfig(observer_lat=37.422, observer_lon=-122.172))
    callsigns = {a["callsign"] for a in provider.get_nearby_aircraft(NOW)}
    assert "PARKED1" not in callsigns
    assert "NOPOS" not in callsigns
    assert "AIH283" not in callsigns, "a negative barometric altitude is not overhead"


def test_an_empty_sky_is_an_empty_list_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: httpx.Response(200, json={"resultCount": 0, "aircraft": []})
    )
    provider = AdsbFiAircraftProvider(AircraftConfig(observer_lat=37.422, observer_lon=-122.172))
    assert provider.get_nearby_aircraft(NOW) == []
    assert provider.last_successful_time == NOW


def test_a_failure_with_no_cache_says_unknown_rather_than_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The same distinction the OpenSky path had to learn: an unreachable feed is not a clear
    # sky, and must not render as "No nearby aircraft".
    monkeypatch.setattr("httpx.get", lambda *a, **k: httpx.Response(503, text="down"))
    provider = AdsbFiAircraftProvider(AircraftConfig(observer_lat=37.422, observer_lon=-122.172))
    with pytest.raises(AircraftDataUnavailable):
        provider.get_nearby_aircraft(NOW)


def test_a_later_failure_serves_cache_and_keeps_the_observation_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_get(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=REAL_RESPONSE)
        return httpx.Response(429, text="Too many requests")

    monkeypatch.setattr("httpx.get", fake_get)
    provider = AdsbFiAircraftProvider(AircraftConfig(observer_lat=37.422, observer_lon=-122.172))
    first = provider.get_nearby_aircraft(NOW)
    assert first

    served = provider.get_nearby_aircraft(NOW + timedelta(hours=1))
    assert served == first, "a stale reading beats nothing"
    assert provider.last_successful_time == NOW, "the timestamp must stay at the observation"


def test_the_cooldown_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_get(*_a, **_k):
        calls["n"] += 1
        return httpx.Response(200, json=REAL_RESPONSE)

    monkeypatch.setattr("httpx.get", fake_get)
    provider = AdsbFiAircraftProvider(
        AircraftConfig(observer_lat=37.422, observer_lon=-122.172, poll_cooldown_seconds=60)
    )
    provider.get_nearby_aircraft(NOW)
    provider.get_nearby_aircraft(NOW + timedelta(seconds=5))
    assert calls["n"] == 1, "the second call was inside the cooldown"


def test_auto_prefers_opensky_only_when_it_has_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The selection is a capability check, not a preference.

    Anonymous OpenSky cannot answer, so an unconfigured door must not be pointed at it. Once
    credentials exist OpenSky is the better source — global and community-run rather than
    dependent on one aggregator — so it takes precedence again automatically.
    """
    from aircraft.provider import OpenSkyAircraftProvider
    from wallboard_worker.scheduler import build_aircraft_provider
    from wallboard_worker.settings import Settings

    for key in ("AIRCRAFT_OBSERVER_LAT", "AIRCRAFT_OBSERVER_LON"):
        monkeypatch.setenv(key, "37.422")
    monkeypatch.setenv("WALLBOARD_WORKER_INGEST_TOKEN", "tok")

    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.setenv("AIRCRAFT_PROVIDER", "auto")
    assert isinstance(build_aircraft_provider(Settings()), AdsbFiAircraftProvider)

    monkeypatch.setenv("OPENSKY_CLIENT_ID", "a-real-client")
    assert isinstance(build_aircraft_provider(Settings()), OpenSkyAircraftProvider)

    # And an explicit choice overrides the check in both directions.
    monkeypatch.setenv("AIRCRAFT_PROVIDER", "adsbfi")
    assert isinstance(build_aircraft_provider(Settings()), AdsbFiAircraftProvider)

    monkeypatch.setenv("AIRCRAFT_PROVIDER", "nonsense")
    with pytest.raises(ValueError):
        build_aircraft_provider(Settings())


@pytest.mark.parametrize("ground_speed_kt", [345.1, 248.3, 0.5, None])
def test_the_mapped_shape_actually_validates_as_a_payload(
    monkeypatch: pytest.MonkeyPatch, ground_speed_kt: float | None
) -> None:
    """The mapper's output has to survive AmbientAircraftNearby, not just look right.

    It did not: `ground_speed_kmh` is `int` in the contract and the mapper produced a float, so
    every poll raised int_from_float and the whole aircraft summary job died — with real
    aircraft in hand and enrichment already fetched. The provider's own tests passed the entire
    time, because they stopped at the dict.
    """
    from doorboard_contracts.events import AmbientAircraftNearby

    entry = dict(REAL_RESPONSE["aircraft"][0])  # type: ignore[index]
    if ground_speed_kt is None:
        entry.pop("gs", None)
    else:
        entry["gs"] = ground_speed_kt

    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: httpx.Response(200, json={"resultCount": 1, "aircraft": [entry]}),
    )
    provider = AdsbFiAircraftProvider(AircraftConfig(observer_lat=37.422, observer_lon=-122.172))
    mapped = provider.get_nearby_aircraft(NOW)
    assert len(mapped) == 1

    # Construct it exactly as run_aircraft_summary does.
    model = AmbientAircraftNearby(
        callsign=mapped[0]["callsign"],
        altitude_ft=mapped[0]["altitude_ft"],
        distance_km=mapped[0]["distance_km"],
        heading=mapped[0]["heading"],
        icao24=mapped[0].get("icao24"),
        latitude=mapped[0].get("latitude"),
        longitude=mapped[0].get("longitude"),
        ground_speed_kmh=mapped[0].get("ground_speed_kmh"),
        vertical_rate_fpm=mapped[0].get("vertical_rate_fpm"),
        on_ground=mapped[0].get("on_ground"),
        registration=mapped[0].get("registration"),
        aircraft_type=mapped[0].get("aircraft_type"),
    )
    assert model.callsign == "FDX1865"
