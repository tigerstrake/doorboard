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


class AircraftProvider(abc.ABC):
    @abc.abstractmethod
    def get_nearby_aircraft(self, now: datetime) -> list[dict[str, Any]]:
        """Calculate and return a list of nearby aircraft detail dicts."""
        pass


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
            return self._cached_aircraft

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
