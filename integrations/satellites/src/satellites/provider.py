from __future__ import annotations

import abc
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

# We import skyfield components lazily or safely
from skyfield.api import EarthSatellite, Loader, load, wgs84

logger = logging.getLogger("doorboard.satellites")


class SatelliteConfig(BaseModel):
    watchlist: list[str] = Field(default_factory=lambda: ["ISS (ZARYA)"])
    observer_lat: float
    observer_lon: float
    observer_elevation: float = 0.0
    min_elevation: float = 10.0
    tle_url: str = "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle"
    tle_cache_path: str = "/tmp/tle_cache.txt"
    # Writable directory for skyfield's ephemeris (de421.bsp). skyfield's global
    # `load` reads/writes the CWD, which the container worker user cannot write
    # ([Errno 13] on 'de421.bsp.download'); an explicit Loader dir fixes that.
    ephemeris_dir: str = "/tmp/skyfield"


class SatelliteProvider(abc.ABC):
    @abc.abstractmethod
    def get_next_pass(self, now: datetime) -> dict[str, Any] | None:
        """Calculate and return the next visible pass payload."""
        pass


class SkyfieldSatelliteProvider(SatelliteProvider):
    def __init__(self, config: SatelliteConfig) -> None:
        self.config = config
        self._eph = None

    def _get_ephemeris(self) -> Any:
        if self._eph is None:
            # Lazy load ephemeris via an explicit Loader pointed at a writable
            # directory (the global `load` uses the CWD, which isn't writable by
            # the container worker user). The Loader reads de421.bsp from the dir
            # and only downloads it once, into that same writable dir, if missing.
            ephemeris_dir = Path(self.config.ephemeris_dir)
            ephemeris_dir.mkdir(parents=True, exist_ok=True)
            # A Loader instance is callable: loader("de421.bsp") loads from its
            # directory and only downloads (into that same dir) when missing.
            loader = Loader(self.config.ephemeris_dir)
            self._eph = loader("de421.bsp")
        return self._eph

    def _get_tles(self) -> dict[str, tuple[str, str]]:
        cache_path = Path(self.config.tle_cache_path)
        use_cache = False

        # Check if cache is fresh
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < 24 * 3600:
                use_cache = True
            elif age < 7 * 24 * 3600:
                # Cache is stale but within 7 days, we can fall back to it if network fails
                pass
            else:
                # Cache is older than 7 days, treat as degraded/stale
                logger.warning(f"TLE cache is too old ({age / 3600:.1f} hours).")

        tle_text = ""
        if not use_cache:
            try:
                resp = httpx.get(self.config.tle_url, timeout=10.0)
                if resp.status_code == 200:
                    tle_text = resp.text
                    # Write to cache
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(tle_text, encoding="utf-8")
                else:
                    logger.warning(
                        f"CelesTrak returned status {resp.status_code}. Using cache fallback."
                    )
            except Exception as e:
                logger.warning(f"Failed to fetch TLEs: {e}. Using cache fallback.")

        if not tle_text and cache_path.exists():
            # Fall back to cache (as long as it exists)
            age = time.time() - cache_path.stat().st_mtime
            if age > 7 * 24 * 3600:
                raise RuntimeError("TLE data is older than 7 days and cannot be trusted.")
            tle_text = cache_path.read_text(encoding="utf-8")

        if not tle_text:
            raise RuntimeError("No TLE data available (fetch failed and no cache exists).")

        # Parse TLEs
        tles = {}
        lines = tle_text.strip().splitlines()
        i = 0
        while i < len(lines):
            line0 = lines[i].strip()
            if i + 2 < len(lines):
                line1 = lines[i + 1].strip()
                line2 = lines[i + 2].strip()
                if line1.startswith("1 ") and line2.startswith("2 "):
                    tles[line0] = (line1, line2)
                    i += 3
                else:
                    i += 1
            else:
                i += 1
        return tles

    def get_next_pass(self, now: datetime) -> dict[str, Any] | None:
        # Load timescale
        ts = load.timescale(builtin=True)

        try:
            tles = self._get_tles()
        except Exception as e:
            logger.error(f"Failed to get TLE data: {e}")
            raise

        observer = wgs84.latlon(
            self.config.observer_lat,
            self.config.observer_lon,
            self.config.observer_elevation,
        )

        eph = self._get_ephemeris()
        earth = eph["earth"]
        sun = eph["sun"]

        # Search window: from now until 24 hours from now
        t0 = ts.from_datetime(now)
        t1 = ts.from_datetime(now + timedelta(hours=24))

        visible_passes = []

        for sat_name in self.config.watchlist:
            if sat_name not in tles:
                continue

            line1, line2 = tles[sat_name]
            satellite = EarthSatellite(line1, line2, sat_name, ts)

            # Find rise/culmination/set events above the horizon
            t_events, y_events = satellite.find_events(
                observer, t0, t1, altitude_degrees=self.config.min_elevation
            )

            # Group events into passes: rise (0), culmination (1), set (2)
            # A valid pass has a rise, culmination, and set.
            # We iterate through the events and build passes.
            i = 0
            while i < len(t_events):
                # Look for a rise event (0)
                if y_events[i] != 0:
                    i += 1
                    continue

                # We found a rise
                rise_time = t_events[i]

                # Find the next culmination (1) and set (2)
                culm_time = None
                set_time = None

                j = i + 1
                while j < len(t_events) and y_events[j] != 0:
                    if y_events[j] == 1:
                        culm_time = t_events[j]
                    elif y_events[j] == 2:
                        set_time = t_events[j]
                        break
                    j += 1

                # If we found culmination and set, process this pass
                if culm_time is not None and set_time is not None:
                    # Check visibility at culmination:
                    # observer in darkness, satellite illuminated
                    observer_loc = earth + observer

                    # 1. Observer darkness (Sun altitude < -6 degrees)
                    sun_pos = observer_loc.at(culm_time).observe(sun).apparent()
                    sun_alt, _, _ = sun_pos.altaz()

                    # 2. Satellite illumination (is the satellite in Earth's shadow?)
                    is_lit = satellite.at(culm_time).is_sunlit(eph)

                    is_dark = sun_alt.degrees < -6.0

                    if is_lit and is_dark:
                        # Calculate maximum elevation and direction at culmination
                        sat_pos = (satellite - observer).at(culm_time)
                        sat_alt, sat_az, _ = sat_pos.altaz()

                        # Determine compass direction
                        az = sat_az.degrees % 360
                        if az < 22.5 or az >= 337.5:
                            direction = "N"
                        elif az < 67.5:
                            direction = "NE"
                        elif az < 112.5:
                            direction = "E"
                        elif az < 157.5:
                            direction = "SE"
                        elif az < 202.5:
                            direction = "S"
                        elif az < 247.5:
                            direction = "SW"
                        elif az < 292.5:
                            direction = "W"
                        else:
                            direction = "NW"

                        visible_passes.append(
                            {
                                "satellite": sat_name,
                                "rise_at": rise_time.utc_datetime(),
                                "max_elevation_deg": round(sat_alt.degrees, 1),
                                "direction": direction,
                                "visible": True,
                            }
                        )
                    i = j + 1
                else:
                    i += 1

        if not visible_passes:
            return None

        # Return the visible pass that rises earliest
        visible_passes.sort(key=lambda x: x["rise_at"])
        return visible_passes[0]


class MockSatelliteProvider(SatelliteProvider):
    def get_next_pass(self, now: datetime) -> dict[str, Any] | None:
        # Mock payload matching satelliteFixture in fixtures.ts
        return {
            "satellite": "ISS",
            "rise_at": now + timedelta(minutes=10),
            "max_elevation_deg": 64.5,
            "direction": "NW",
            "visible": True,
        }
