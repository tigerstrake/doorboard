from __future__ import annotations

import abc
import logging
import math
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger("doorboard.aircraft")


class AircraftConfig(BaseModel):
    observer_lat: float
    observer_lon: float
    bbox_half_size_lat: float = 0.25
    bbox_half_size_lon: float = 0.25
    opensky_username: str = ""
    opensky_password: str = ""
    opensky_url: str = "https://opensky-network.org/api/states/all"
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

        auth = None
        if self.config.opensky_username and self.config.opensky_password:
            auth = (self.config.opensky_username, self.config.opensky_password)

        self._last_request_time = now
        try:
            resp = httpx.get(self.config.opensky_url, params=params, auth=auth, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                states = data.get("states") or []
                nearby = []
                for s in states:
                    # Index values check OpenSky API doc:
                    # 1: callsign, 5: longitude, 6: latitude,
                    # 7: baro_alt, 8: on_ground, 10: true_track
                    callsign = (s[1] or "").strip()
                    lon = s[5]
                    lat = s[6]
                    alt_m = s[7] if s[7] is not None else s[13]  # Fallback to geo_altitude
                    on_ground = s[8]
                    track = s[10]

                    if lat is None or lon is None or on_ground:
                        continue

                    dist = haversine_distance(
                        self.config.observer_lat, self.config.observer_lon, lat, lon
                    )
                    alt_ft = int(alt_m * 3.28084) if alt_m is not None else 0
                    heading = int(track) if track is not None else 0

                    nearby.append(
                        {
                            "callsign": callsign,
                            "altitude_ft": alt_ft,
                            "distance_km": round(dist, 2),
                            "heading": heading,
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
