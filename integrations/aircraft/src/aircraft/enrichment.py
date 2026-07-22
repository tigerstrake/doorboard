"""Best-effort external enrichment for nearby aircraft.

Given the OpenSky-derived aircraft dicts produced by
:class:`aircraft.provider.OpenSkyAircraftProvider`, this module fills in richer
per-plane detail from two free, no-API-key public services:

* **adsbdb** (https://api.adsbdb.com) — registration, aircraft type/manufacturer,
  operator (via ``/v0/aircraft/{icao24}``) and route origin/destination airports
  (via ``/v0/callsign/{callsign}``).
* **planespotters** (https://api.planespotters.net) — a thumbnail photo URL plus
  photographer attribution (via ``/pub/photos/hex/{icao24}``).

Everything here is strictly best-effort: enrichment enhances the summary but must
never break it. Any HTTP error, timeout, unexpected shape, or a global deadline
overrun leaves the affected field(s) simply absent, and callers still emit the
basic OpenSky summary. Only the nearest N aircraft are enriched (the input is
distance-sorted) and results are cached per-icao24/callsign so repeated polls do
not re-hit the upstreams. See ADR-0015.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("doorboard.aircraft.enrichment")

ADSBDB_BASE = "https://api.adsbdb.com/v0"
PLANESPOTTERS_HEX_BASE = "https://api.planespotters.net/pub/photos/hex"

DEFAULT_USER_AGENT = (
    "doorboard-wallboard-worker/1.0 "
    "(+https://github.com/tigerstrake/doorboard; ambient aircraft enrichment)"
)


@dataclass(frozen=True)
class EnrichmentConfig:
    """Tunables for :class:`AircraftEnricher`."""

    enabled: bool = True
    # Enrich at most this many (nearest, since input is distance-sorted) planes.
    max_aircraft: int = 6
    # Per-request timeout. Kept short so a slow upstream can't stall the poll.
    request_timeout_s: float = 3.0
    # Global wall-clock budget for one enrich() pass. Checked before every
    # request so a whole run stays comfortably under the ~30s aircraft poll even
    # if many upstream calls hang up to request_timeout_s each.
    total_timeout_s: float = 12.0
    # Registration/type/photo change rarely -> cache for hours. Routes can change
    # per flight -> cache for minutes.
    aircraft_cache_ttl_s: float = 6 * 3600.0
    photo_cache_ttl_s: float = 6 * 3600.0
    route_cache_ttl_s: float = 5 * 60.0
    user_agent: str = DEFAULT_USER_AGENT


# Fields written by adsbdb aircraft enrichment.
_AIRCRAFT_FIELDS = ("registration", "aircraft_type", "operator")
# Fields written by adsbdb callsign (route) enrichment.
_ROUTE_FIELDS = ("origin", "destination")
# Fields written by planespotters photo enrichment.
_PHOTO_FIELDS = ("photo_url", "photo_attribution")


@dataclass
class _CacheEntry:
    expires_monotonic: float
    value: dict[str, Any]


class AircraftEnricher:
    """Merge cached, best-effort external detail into aircraft dicts.

    The enricher is intended to be long-lived (one per worker process) so its
    in-memory TTL caches survive across polls. A fresh :class:`httpx.Client` is
    created per :meth:`enrich` call and always closed.
    """

    def __init__(
        self,
        config: EnrichmentConfig | None = None,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config or EnrichmentConfig()
        self._client_factory = client_factory or self._default_client_factory
        self._monotonic = monotonic
        self._aircraft_cache: dict[str, _CacheEntry] = {}
        self._route_cache: dict[str, _CacheEntry] = {}
        self._photo_cache: dict[str, _CacheEntry] = {}

    def _default_client_factory(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.config.request_timeout_s,
            headers={"User-Agent": self.config.user_agent},
        )

    def enrich(self, aircraft: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Enrich (in place) the nearest ``max_aircraft`` entries and return the list.

        Never raises: any failure is logged and swallowed so the caller can still
        emit the basic summary.
        """
        if not self.config.enabled:
            return aircraft

        targets = [ac for ac in aircraft if ac.get("icao24")][: self.config.max_aircraft]
        if not targets:
            return aircraft

        deadline = self._monotonic() + self.config.total_timeout_s
        try:
            client = self._client_factory()
        except Exception as exc:  # pragma: no cover - client construction is trivial
            logger.warning("aircraft enrichment client init failed: %s", exc)
            return aircraft

        try:
            for entry in targets:
                if self._monotonic() >= deadline:
                    logger.debug("aircraft enrichment deadline reached; stopping early")
                    break
                self._enrich_one(client, entry, deadline)
        except Exception as exc:  # defensive: never let enrichment break the summary
            logger.warning("aircraft enrichment aborted: %s", exc)
        finally:
            with contextlib.suppress(Exception):  # pragma: no cover
                client.close()
        return aircraft

    def _enrich_one(self, client: httpx.Client, entry: dict[str, Any], deadline: float) -> None:
        icao24 = str(entry["icao24"]).strip().lower()
        callsign = str(entry.get("callsign") or "").strip()

        aircraft_info = self._cached(
            self._aircraft_cache,
            icao24,
            self.config.aircraft_cache_ttl_s,
            lambda: self._fetch_aircraft(client, icao24),
            deadline,
        )
        self._merge(entry, aircraft_info, _AIRCRAFT_FIELDS)

        if callsign:
            route_info = self._cached(
                self._route_cache,
                callsign,
                self.config.route_cache_ttl_s,
                lambda: self._fetch_route(client, callsign),
                deadline,
            )
            self._merge(entry, route_info, _ROUTE_FIELDS)

        photo_info = self._cached(
            self._photo_cache,
            icao24,
            self.config.photo_cache_ttl_s,
            lambda: self._fetch_photo(client, icao24),
            deadline,
        )
        self._merge(entry, photo_info, _PHOTO_FIELDS)

    @staticmethod
    def _merge(entry: dict[str, Any], info: dict[str, Any], fields: tuple[str, ...]) -> None:
        for key in fields:
            value = info.get(key)
            if value is not None:
                entry[key] = value

    def _cached(
        self,
        cache: dict[str, _CacheEntry],
        key: str,
        ttl_s: float,
        fetch: Callable[[], dict[str, Any]],
        deadline: float,
    ) -> dict[str, Any]:
        """Return cached data for ``key`` or fetch it once (caching hits & misses)."""
        now = self._monotonic()
        hit = cache.get(key)
        if hit is not None and hit.expires_monotonic > now:
            return hit.value
        # Respect the global budget: skip a fresh lookup once we're out of time.
        if now >= deadline:
            return hit.value if hit is not None else {}
        try:
            value = fetch()
        except Exception as exc:
            logger.debug("aircraft enrichment lookup failed for %s: %s", key, exc)
            value = {}
        cache[key] = _CacheEntry(expires_monotonic=now + ttl_s, value=value)
        return value

    # --- upstream fetchers -------------------------------------------------

    def _fetch_aircraft(self, client: httpx.Client, icao24: str) -> dict[str, Any]:
        resp = client.get(f"{ADSBDB_BASE}/aircraft/{icao24}")
        if resp.status_code != 200:
            return {}
        aircraft = (resp.json() or {}).get("response", {}).get("aircraft") or {}
        if not isinstance(aircraft, dict):
            return {}
        manufacturer = _clean(aircraft.get("manufacturer"))
        type_name = _clean(aircraft.get("type"))
        if manufacturer and type_name:
            aircraft_type = f"{manufacturer} {type_name}"
        else:
            aircraft_type = type_name or manufacturer
        result: dict[str, Any] = {}
        registration = _clean(aircraft.get("registration"))
        operator = _clean(aircraft.get("registered_owner"))
        if registration:
            result["registration"] = registration
        if aircraft_type:
            result["aircraft_type"] = aircraft_type
        if operator:
            result["operator"] = operator
        return result

    def _fetch_route(self, client: httpx.Client, callsign: str) -> dict[str, Any]:
        resp = client.get(f"{ADSBDB_BASE}/callsign/{callsign}")
        if resp.status_code != 200:
            return {}
        route = (resp.json() or {}).get("response", {}).get("flightroute") or {}
        if not isinstance(route, dict):
            return {}
        result: dict[str, Any] = {}
        origin = _format_airport(route.get("origin"))
        destination = _format_airport(route.get("destination"))
        if origin:
            result["origin"] = origin
        if destination:
            result["destination"] = destination
        return result

    def _fetch_photo(self, client: httpx.Client, icao24: str) -> dict[str, Any]:
        resp = client.get(f"{PLANESPOTTERS_HEX_BASE}/{icao24}")
        if resp.status_code != 200:
            return {}
        photos = (resp.json() or {}).get("photos") or []
        if not photos or not isinstance(photos, list):
            return {}
        photo = photos[0]
        if not isinstance(photo, dict):
            return {}
        thumb = photo.get("thumbnail_large") or photo.get("thumbnail") or {}
        photo_url = _clean(thumb.get("src")) if isinstance(thumb, dict) else None
        if not photo_url:
            return {}
        result: dict[str, Any] = {"photo_url": photo_url}
        photographer = _clean(photo.get("photographer"))
        attribution = f"{photographer} / planespotters.net" if photographer else "planespotters.net"
        result["photo_attribution"] = attribution
        return result


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _format_airport(airport: Any) -> str | None:
    """Render an adsbdb airport object as e.g. ``"San Francisco (SFO)"``."""
    if not isinstance(airport, dict):
        return None
    name = _clean(airport.get("municipality")) or _clean(airport.get("name"))
    code = _clean(airport.get("iata_code")) or _clean(airport.get("icao_code"))
    if name and code:
        return f"{name} ({code})"
    return name or code


def build_enricher(
    *,
    enabled: bool = True,
    max_aircraft: int = 6,
) -> AircraftEnricher | None:
    """Construct an enricher from worker settings, or None when disabled.

    Timeouts and cache TTLs use :class:`EnrichmentConfig` defaults; only the two
    operator-facing knobs (enabled / max) are wired to env today.
    """
    if not enabled:
        return None
    return AircraftEnricher(EnrichmentConfig(enabled=True, max_aircraft=max_aircraft))
