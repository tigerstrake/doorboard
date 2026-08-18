from __future__ import annotations

import abc
import logging
import math
from datetime import datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger("doorboard.aircraft")

# OpenSky's OAuth2 client-credentials token endpoint. Basic auth (username/
# password) is no longer accepted by OpenSky, so credentials go through here.
OPENSKY_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
)


class AircraftConfig(BaseModel):
    observer_lat: float
    observer_lon: float
    bbox_half_size_lat: float = 0.25
    bbox_half_size_lon: float = 0.25
    # OpenSky OAuth2 client credentials (register an API client on OpenSky).
    # Leave empty for anonymous access, which still works but is heavily
    # throttled (~400 daily credits, most-recent state vectors only).
    opensky_client_id: str = ""
    opensky_client_secret: str = ""
    opensky_url: str = "https://opensky-network.org/api/states/all"
    opensky_token_url: str = OPENSKY_TOKEN_URL
    poll_cooldown_seconds: int = 30
    # adsb.fi's open feed: no key, point-and-radius instead of a bounding box. Used when
    # OpenSky has no credentials, because anonymous OpenSky cannot sustain any useful cadence.
    adsbfi_url: str = "https://opendata.adsb.fi/api/v2"
    radius_nm: int = 50
    request_timeout_s: float = 10.0


class AircraftDataUnavailable(RuntimeError):
    """Raised when there is no aircraft data to report — not even stale.

    Distinct from an empty list, which is a real observation: "the sky over the bay is
    clear". Returning the empty cache on a failed fetch conflated the two, and the wallboard
    published it as the confident claim "No nearby aircraft" while OpenSky was in fact
    answering 429 to every poll. An empty sky and a rate-limited API must not look the same.
    """


class AircraftProvider(abc.ABC):
    @abc.abstractmethod
    def get_nearby_aircraft(self, now: datetime) -> list[dict[str, Any]]:
        """Nearby aircraft, or raise :class:`AircraftDataUnavailable` if none are known.

        An empty list means the sky is clear. It does not mean "the lookup failed".
        """
        pass

    @property
    def last_successful_time(self) -> datetime | None:
        """When the returned data was actually observed, if known.

        The caller stamps the payload with this rather than with "now", so cached data
        cannot present itself as current.
        """
        return None


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points in kilometers."""
    R = 6371.0  # Earth's radius in km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


class OpenSkyAircraftProvider(AircraftProvider):
    def __init__(self, config: AircraftConfig) -> None:
        self.config = config
        self._cached_aircraft: list[dict[str, Any]] = []
        self._last_request_time: datetime | None = None
        self._last_successful_time: datetime | None = None
        self._token: str | None = None
        self._token_expiry: datetime | None = None

    def _has_credentials(self) -> bool:
        return bool(self.config.opensky_client_id and self.config.opensky_client_secret)

    def _get_token(self, now: datetime, *, force: bool = False) -> str | None:
        """Fetch/cache an OAuth2 client-credentials bearer token (~30 min TTL).

        Returns None (→ anonymous request) when unconfigured or on failure, so a
        token outage degrades gracefully rather than dropping the feed.
        """
        if not self._has_credentials():
            return None
        if (
            not force
            and self._token is not None
            and self._token_expiry is not None
            and now < self._token_expiry
        ):
            return self._token
        try:
            resp = httpx.post(
                self.config.opensky_token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.config.opensky_client_id,
                    "client_secret": self.config.opensky_client_secret,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            payload = resp.json()
            self._token = payload["access_token"]
            expires_in = int(payload.get("expires_in", 1800))
            # Refresh a minute early so we never send an about-to-expire token.
            self._token_expiry = now + timedelta(seconds=max(expires_in - 60, 30))
            return self._token
        except Exception as e:
            logger.warning(f"OpenSky token fetch failed: {e}. Falling back to anonymous.")
            self._token = None
            self._token_expiry = None
            return None

    def get_nearby_aircraft(self, now: datetime) -> list[dict[str, Any]]:
        # Respect cooldown/rate limits
        time_since_last = None
        if self._last_request_time is not None:
            time_since_last = (now - self._last_request_time).total_seconds()

        # If we are cooling down, serve cache directly without making request
        if time_since_last is not None and time_since_last < self.config.poll_cooldown_seconds:
            logger.debug("Request within cooldown period. Serving cached aircraft data.")
            return self._serve_cache("cooldown")

        # Bounding box coordinates
        lamin = self.config.observer_lat - self.config.bbox_half_size_lat
        lamax = self.config.observer_lat + self.config.bbox_half_size_lat
        lomin = self.config.observer_lon - self.config.bbox_half_size_lon
        lomax = self.config.observer_lon + self.config.bbox_half_size_lon

        params = {
            "lamin": lamin,
            "lamax": lamax,
            "lomin": lomin,
            "lomax": lomax,
        }

        headers: dict[str, str] = {}
        token = self._get_token(now)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        self._last_request_time = now
        try:
            resp = httpx.get(self.config.opensky_url, params=params, headers=headers, timeout=10.0)
            # A 401 means the token expired mid-flight — refresh once and retry.
            if resp.status_code == 401 and self._has_credentials():
                token = self._get_token(now, force=True)
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                    resp = httpx.get(
                        self.config.opensky_url, params=params, headers=headers, timeout=10.0
                    )
            if resp.status_code == 200:
                data = resp.json()
                states = data.get("states") or []
                nearby = []
                for s in states:
                    # OpenSky state-vector indices (see states/all API doc):
                    # 0: icao24, 1: callsign, 2: origin_country, 5: longitude,
                    # 6: latitude, 7: baro_altitude, 8: on_ground, 9: velocity
                    # (m/s), 10: true_track, 11: vertical_rate (m/s),
                    # 13: geo_altitude.
                    icao24 = (s[0] or "").strip().lower() or None
                    callsign = (s[1] or "").strip()
                    origin_country = s[2]
                    lon = s[5]
                    lat = s[6]
                    alt_m = s[7] if s[7] is not None else s[13]  # Fallback to geo_altitude
                    on_ground = s[8]
                    velocity = s[9]
                    track = s[10]
                    vertical_rate = s[11]

                    if lat is None or lon is None or on_ground:
                        continue

                    dist = haversine_distance(
                        self.config.observer_lat, self.config.observer_lon, lat, lon
                    )
                    alt_ft = int(alt_m * 3.28084) if alt_m is not None else 0
                    heading = int(track) if track is not None else 0
                    # velocity m/s -> km/h; vertical_rate m/s -> feet/min.
                    ground_speed_kmh = round(velocity * 3.6) if velocity is not None else None
                    vertical_rate_fpm = (
                        round(vertical_rate * 196.85) if vertical_rate is not None else None
                    )

                    nearby.append(
                        {
                            "callsign": callsign,
                            "altitude_ft": alt_ft,
                            "distance_km": round(dist, 2),
                            "heading": heading,
                            "icao24": icao24,
                            "latitude": lat,
                            "longitude": lon,
                            "ground_speed_kmh": ground_speed_kmh,
                            "vertical_rate_fpm": vertical_rate_fpm,
                            "on_ground": bool(on_ground),
                            "origin_country": origin_country,
                        }
                    )

                # Sort by distance
                nearby.sort(key=lambda x: x["distance_km"])
                self._cached_aircraft = nearby
                self._last_successful_time = now
                return nearby
            elif resp.status_code == 429:
                logger.warning(
                    "OpenSky API returned HTTP 429 (Rate Limit). Serving cached aircraft data."
                )
            else:
                logger.warning(
                    f"OpenSky API returned HTTP {resp.status_code}. Serving cached aircraft data."
                )
        except Exception as e:
            logger.warning(f"Error fetching from OpenSky API: {e}. Serving cached aircraft data.")

        return self._serve_cache("fetch failed")

    @property
    def last_successful_time(self) -> datetime | None:
        return self._last_successful_time

    def _serve_cache(self, why: str) -> list[dict[str, Any]]:
        """The cache, or an explicit "unknown" when there has never been a successful fetch.

        `self._cached_aircraft` starts empty, so before the first success this used to hand
        back `[]` — which the wallboard renders as "No nearby aircraft." OpenSky answering
        429 to every request is not an empty sky.
        """
        if self._last_successful_time is None:
            raise AircraftDataUnavailable(
                f"no aircraft data has been retrieved successfully yet ({why})"
            )
        return self._cached_aircraft


class MockAircraftProvider(AircraftProvider):
    def get_nearby_aircraft(self, now: datetime) -> list[dict[str, Any]]:
        return [
            {
                "callsign": "UAL123",
                "altitude_ft": 12500,
                "distance_km": 15.42,
                "heading": 180,
            },
            {
                "callsign": "SWR45",
                "altitude_ft": 32000,
                "distance_km": 28.1,
                "heading": 95,
            },
        ]


# Below this, an aircraft is taxiing or parked rather than overhead. Nothing meaningfully
# "nearby and in the sky" is under a couple of hundred feet.
GROUND_ALTITUDE_GATE_FT = 200.0


class AdsbFiAircraftProvider(AircraftProvider):
    """Nearby aircraft from adsb.fi's open ADS-B feed.

    Exists because OpenSky's anonymous tier cannot do this job. Without registered
    credentials it allows a few hundred requests per IP per day, and on the owner's door it
    answered HTTP 429 to *every* poll, with the API itself reporting
    ``x-rate-limit-retry-after-seconds: 16841`` — nearly five hours. A wallboard that says
    nothing about the sky over a Bay Area holding hundreds of aircraft is not a rate-limit
    problem the operator can tune their way out of; it needs a source that will answer.

    adsb.fi needs no key, takes a point and a radius rather than a bounding box, and returns
    *more* than OpenSky does: registration and type arrive inline, which the enrichment step
    otherwise fetches from two more services.

    OpenSky remains the better source when credentials exist — it is global and
    community-run rather than dependent on one aggregator's goodwill — so the scheduler
    prefers it whenever a client id is configured. This is the fallback that makes an
    unconfigured door work.
    """

    def __init__(self, config: AircraftConfig) -> None:
        self.config = config
        self._cached_aircraft: list[dict[str, Any]] = []
        self._last_request_time: datetime | None = None
        self._last_successful_time: datetime | None = None

    @property
    def last_successful_time(self) -> datetime | None:
        return self._last_successful_time

    def get_nearby_aircraft(self, now: datetime) -> list[dict[str, Any]]:
        if self._last_request_time is not None:
            elapsed = (now - self._last_request_time).total_seconds()
            if elapsed < self.config.poll_cooldown_seconds:
                logger.debug("Within cooldown period. Serving cached aircraft data.")
                return self._serve_cache("cooldown")

        url = (
            f"{self.config.adsbfi_url.rstrip('/')}"
            f"/lat/{self.config.observer_lat}/lon/{self.config.observer_lon}"
            f"/dist/{self.config.radius_nm}"
        )
        self._last_request_time = now
        try:
            resp = httpx.get(url, timeout=self.config.request_timeout_s)
            if resp.status_code != 200:
                logger.warning(
                    f"adsb.fi returned HTTP {resp.status_code}. Serving cached aircraft data."
                )
                return self._serve_cache(f"HTTP {resp.status_code}")

            nearby: list[dict[str, Any]] = []
            for entry in resp.json().get("aircraft", []):
                mapped = self._map_aircraft(entry)
                if mapped is not None:
                    nearby.append(mapped)
            nearby.sort(key=lambda item: item["distance_km"])
            self._cached_aircraft = nearby
            self._last_successful_time = now
            return nearby
        except Exception as exc:
            logger.warning(f"Error fetching from adsb.fi: {exc}. Serving cached aircraft data.")
            return self._serve_cache("fetch failed")

    def _map_aircraft(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        """Map one adsb.fi record onto the shape the summary job expects.

        Returns None for anything that cannot be placed or is sitting on the ground: a
        wallboard tile called "overhead aircraft" that leads with parked jets at the nearest
        airport is answering a different question than the one asked.
        """
        lat, lon = entry.get("lat"), entry.get("lon")
        if lat is None or lon is None:
            return None
        altitude = entry.get("alt_baro")
        if altitude == "ground" or altitude is None:
            return None
        # Some feeds report a *numeric* barometric altitude for an aircraft that is on the
        # ground — near sea level it can even read negative. Seen live: an Asiana 747 at
        # "-75 ft", 28 km away, sitting at SFO and listed among the overhead aircraft. The
        # string check above does not catch those, so anything below the gate is on the
        # ground as far as this tile is concerned.
        if float(altitude) < GROUND_ALTITUDE_GATE_FT:
            return None

        callsign = (entry.get("flight") or "").strip()
        ground_speed_kt = entry.get("gs")
        return {
            "callsign": callsign or (entry.get("hex") or "unknown"),
            "altitude_ft": int(altitude),
            "distance_km": round(
                haversine_distance(self.config.observer_lat, self.config.observer_lon, lat, lon), 2
            ),
            "heading": int(entry.get("track") or 0),
            "icao24": entry.get("hex"),
            "latitude": lat,
            "longitude": lon,
            # adsb.fi reports knots; the contract is km/h, and it is an *int* — a float here
            # raised int_from_float and took the whole summary job down with it.
            "ground_speed_kmh": (
                round(float(ground_speed_kt) * 1.852) if ground_speed_kt is not None else None
            ),
            "vertical_rate_fpm": entry.get("baro_rate"),
            "on_ground": False,
            # Inline, where OpenSky needs two more services to supply them.
            "registration": entry.get("r"),
            "aircraft_type": entry.get("t"),
        }

    def _serve_cache(self, why: str) -> list[dict[str, Any]]:
        """The cache, or an explicit "unknown" if nothing has ever been fetched."""
        if self._last_successful_time is None:
            raise AircraftDataUnavailable(
                f"no aircraft data has been retrieved successfully yet ({why})"
            )
        return self._cached_aircraft
