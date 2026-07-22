from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import httpx
from aircraft.enrichment import AircraftEnricher, EnrichmentConfig

# --- canned upstream responses (no real network) ---------------------------

ADSBDB_AIRCRAFT = {
    "response": {
        "aircraft": {
            "registration": "HB-JMA",
            "manufacturer": "Airbus",
            "type": "A340-313",
            "registered_owner": "Swiss International Air Lines",
        }
    }
}

ADSBDB_ROUTE = {
    "response": {
        "flightroute": {
            "origin": {"municipality": "Zurich", "iata_code": "ZRH", "icao_code": "LSZH"},
            "destination": {"municipality": "San Francisco", "iata_code": "SFO"},
        }
    }
}

PLANESPOTTERS_PHOTO = {
    "photos": [
        {
            "thumbnail_large": {"src": "https://cdn.planespotters.net/hb-jma-large.jpg"},
            "thumbnail": {"src": "https://cdn.planespotters.net/hb-jma-small.jpg"},
            "photographer": "Jane Spotter",
        }
    ]
}


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Minimal stand-in for httpx.Client that routes by URL fragment."""

    def __init__(
        self,
        routes: dict[str, _FakeResponse] | None = None,
        *,
        raise_exc: Exception | None = None,
    ) -> None:
        self._routes = routes or {}
        self._raise_exc = raise_exc
        self.calls: list[str] = []
        self.closed = False

    def get(self, url: str) -> _FakeResponse:
        self.calls.append(url)
        if self._raise_exc is not None:
            raise self._raise_exc
        for fragment, response in self._routes.items():
            if fragment in url:
                return response
        return _FakeResponse(404, {})

    def close(self) -> None:
        self.closed = True


def _factory(client: _FakeClient) -> Callable[[], httpx.Client]:
    return cast("Callable[[], httpx.Client]", lambda: client)


def _full_routes() -> dict[str, _FakeResponse]:
    return {
        "/aircraft/": _FakeResponse(200, ADSBDB_AIRCRAFT),
        "/callsign/": _FakeResponse(200, ADSBDB_ROUTE),
        "planespotters": _FakeResponse(200, PLANESPOTTERS_PHOTO),
    }


def test_enrichment_merges_mocked_responses() -> None:
    client = _FakeClient(_full_routes())
    enricher = AircraftEnricher(EnrichmentConfig(), client_factory=_factory(client))

    aircraft: list[dict[str, Any]] = [
        {"callsign": "SWR123", "distance_km": 3.0, "icao24": "4b1812"}
    ]
    result = enricher.enrich(aircraft)

    ac = result[0]
    assert ac["registration"] == "HB-JMA"
    assert ac["aircraft_type"] == "Airbus A340-313"  # manufacturer + type folded
    assert ac["operator"] == "Swiss International Air Lines"
    assert ac["origin"] == "Zurich (ZRH)"
    assert ac["destination"] == "San Francisco (SFO)"
    assert ac["photo_url"] == "https://cdn.planespotters.net/hb-jma-large.jpg"
    assert ac["photo_attribution"] == "Jane Spotter / planespotters.net"
    assert client.closed is True


def test_enrichment_swallows_failures() -> None:
    client = _FakeClient(raise_exc=httpx.ConnectError("boom"))
    enricher = AircraftEnricher(EnrichmentConfig(), client_factory=_factory(client))

    aircraft: list[dict[str, Any]] = [
        {"callsign": "SWR123", "distance_km": 3.0, "icao24": "4b1812"}
    ]
    # Must not raise, and the basic fields are left untouched with nothing added.
    result = enricher.enrich(aircraft)

    ac = result[0]
    assert ac["callsign"] == "SWR123"
    assert "registration" not in ac
    assert "photo_url" not in ac
    assert client.closed is True


def test_enrichment_caches_and_dedupes_lookups() -> None:
    client = _FakeClient(_full_routes())
    enricher = AircraftEnricher(EnrichmentConfig(), client_factory=_factory(client))

    # Two planes sharing icao24 + callsign: the second is fully served from cache.
    aircraft: list[dict[str, Any]] = [
        {"callsign": "SWR123", "distance_km": 1.0, "icao24": "4b1812"},
        {"callsign": "SWR123", "distance_km": 2.0, "icao24": "4b1812"},
    ]
    enricher.enrich(aircraft)
    # Exactly one lookup per source (aircraft, callsign, photo) despite two planes.
    assert len(client.calls) == 3

    # A subsequent pass within the TTL adds no new upstream calls.
    enricher.enrich(aircraft)
    assert len(client.calls) == 3


def test_enrichment_disabled_makes_no_calls() -> None:
    client = _FakeClient(_full_routes())
    enricher = AircraftEnricher(EnrichmentConfig(enabled=False), client_factory=_factory(client))
    aircraft: list[dict[str, Any]] = [
        {"callsign": "SWR123", "distance_km": 3.0, "icao24": "4b1812"}
    ]
    result = enricher.enrich(aircraft)
    assert result[0] == aircraft[0]
    assert client.calls == []


def test_enrichment_respects_max_and_skips_missing_icao24() -> None:
    client = _FakeClient(_full_routes())
    enricher = AircraftEnricher(EnrichmentConfig(max_aircraft=1), client_factory=_factory(client))
    aircraft: list[dict[str, Any]] = [
        {"callsign": "AAA", "distance_km": 1.0, "icao24": "aaaaaa"},
        {"callsign": "BBB", "distance_km": 2.0, "icao24": "bbbbbb"},
        {"callsign": "CCC", "distance_km": 3.0},  # no icao24 -> never a target
    ]
    enricher.enrich(aircraft)

    # Only the single nearest icao24 plane is enriched (3 calls total).
    assert aircraft[0].get("registration") == "HB-JMA"
    assert "registration" not in aircraft[1]
    assert "registration" not in aircraft[2]
    assert len(client.calls) == 3


def test_enrichment_handles_non_200_and_empty_photos() -> None:
    routes = {
        "/aircraft/": _FakeResponse(404, {}),
        "/callsign/": _FakeResponse(200, {"response": {"flightroute": {}}}),
        "planespotters": _FakeResponse(200, {"photos": []}),
    }
    client = _FakeClient(routes)
    enricher = AircraftEnricher(EnrichmentConfig(), client_factory=_factory(client))

    aircraft: list[dict[str, Any]] = [
        {"callsign": "SWR123", "distance_km": 3.0, "icao24": "4b1812"}
    ]
    result = enricher.enrich(aircraft)

    ac = result[0]
    assert "registration" not in ac
    assert "origin" not in ac
    assert "photo_url" not in ac
