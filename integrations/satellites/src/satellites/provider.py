from __future__ import annotations

import abc
import logging
import math
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

    # --- Full-orbit tracking (ADR-0041) ---
    # The interesting satellites to draw whole ground tracks + live positions for, selected
    # by NORAD catalog number (stable, unlike the CelesTrak name). Default: ISS, Tiangong/CSS,
    # Hubble, and two bright NOAA birds. Both the id set and the TLE group are configurable.
    orbit_norad_ids: list[int] = Field(default_factory=lambda: [25544, 48274, 20580, 25338, 28654])
    # The "visual" group is CelesTrak's brightest-objects catalogue and contains the marquee
    # ids above; an id that is not in the fetched set is skipped, not fatal. Cached like the
    # pass TLEs (~24 h) so this never hammers CelesTrak.
    orbit_tle_url: str = "https://celestrak.org/NORAD/elements/gp.php?GROUP=visual&FORMAT=tle"
    orbit_tle_cache_path: str = "/tmp/orbit_tle_cache.txt"
    # Points per full orbital period. ~120 draws a smooth loop without bloating the event.
    orbit_samples: int = 120


class SatelliteProvider(abc.ABC):
    @abc.abstractmethod
    def get_next_pass(self, now: datetime) -> dict[str, Any] | None:
        """Calculate and return the next visible pass payload."""
        pass

    @abc.abstractmethod
    def get_orbits(self, now: datetime) -> list[dict[str, Any]]:
        """Full-period ground tracks + current sub-points for the tracked set (ADR-0041).

        Each entry: ``{name, norad_id, sub_lat, sub_lng, track: [{at, lat, lng}, ...]}`` where
        ``track`` covers roughly one orbital period with absolute UTC sample times, so a client
        can wrap "now" into the period and interpolate a live marker without a re-publish.
        Returns ``[]`` when nothing can be computed rather than raising for an empty result.
        """
        pass


class SkyfieldSatelliteProvider(SatelliteProvider):
    def __init__(self, config: SatelliteConfig) -> None:
        self.config = config
        self._eph = None

    # How many points describe one pass. A pass is a few minutes long and the arc is
    # smooth, so ~24 samples draw it cleanly without bloating an event that crosses MQTT
    # and lands in the NUC archive. Consumers are told not to rely on the count.
    TRACK_SAMPLES = 24

    def _sample_track(
        self, satellite: Any, observer: Any, rise_time: Any, set_time: Any
    ) -> list[dict[str, float]]:
        """Sample alt/az evenly between rise and set.

        Elevation is clamped at 0: rounding at the endpoints can put the satellite a
        hair below the horizon, and a negative elevation would draw outside the sky dome.
        """
        rise_tt = rise_time.tt
        set_tt = set_time.tt
        if set_tt <= rise_tt:
            return []
        span_days = set_tt - rise_tt
        span_seconds = span_days * 86400.0
        ts = rise_time.ts
        samples: list[dict[str, float]] = []
        for index in range(self.TRACK_SAMPLES + 1):
            fraction = index / self.TRACK_SAMPLES
            moment = ts.tt_jd(rise_tt + span_days * fraction)
            alt, az, _ = (satellite - observer).at(moment).altaz()
            # The sub-satellite point, from the same moment as the alt/az above: the spot on
            # Earth it is directly over. A globe needs this and cannot derive it from a
            # bearing and an elevation without knowing the orbit (ADR-0030).
            subpoint = wgs84.subpoint(satellite.at(moment))
            samples.append(
                {
                    "t_offset_s": round(span_seconds * fraction, 1),
                    "azimuth_deg": round(az.degrees % 360, 1),
                    "elevation_deg": round(max(0.0, alt.degrees), 1),
                    "lat": round(subpoint.latitude.degrees, 3),
                    "lng": round(subpoint.longitude.degrees, 3),
                }
            )
        return samples

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

    def _get_tles(
        self, url: str | None = None, cache_path: str | None = None
    ) -> dict[str, tuple[str, str]]:
        # url/cache_path default to the pass (stations) source; get_orbits passes the orbit
        # (visual) source so the two feeds cache independently and neither hammers CelesTrak.
        tle_url = url or self.config.tle_url
        cache_file = Path(cache_path or self.config.tle_cache_path)
        use_cache = False

        # Check if cache is fresh
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
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
                resp = httpx.get(tle_url, timeout=10.0)
                if resp.status_code == 200:
                    tle_text = resp.text
                    # Write to cache
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(tle_text, encoding="utf-8")
                else:
                    logger.warning(
                        f"CelesTrak returned status {resp.status_code}. Using cache fallback."
                    )
            except Exception as e:
                logger.warning(f"Failed to fetch TLEs: {e}. Using cache fallback.")

        if not tle_text and cache_file.exists():
            # Fall back to cache (as long as it exists)
            age = time.time() - cache_file.stat().st_mtime
            if age > 7 * 24 * 3600:
                raise RuntimeError("TLE data is older than 7 days and cannot be trusted.")
            tle_text = cache_file.read_text(encoding="utf-8")

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

                        # Sample the arc from rise to set. The events above already
                        # bound the pass, so this is the shape of it: what the wallboard
                        # needs to draw where to look and for how long. Previously
                        # computed and thrown away in favour of one compass point.
                        rise_alt, rise_az, _ = (satellite - observer).at(rise_time).altaz()
                        set_alt, set_az, _ = (satellite - observer).at(set_time).altaz()
                        track = self._sample_track(satellite, observer, rise_time, set_time)

                        visible_passes.append(
                            {
                                "satellite": sat_name,
                                "rise_at": rise_time.utc_datetime(),
                                "max_elevation_deg": round(sat_alt.degrees, 1),
                                "direction": direction,
                                "visible": True,
                                "set_at": set_time.utc_datetime(),
                                "rise_azimuth_deg": round(rise_az.degrees % 360, 1),
                                "set_azimuth_deg": round(set_az.degrees % 360, 1),
                                "culmination_azimuth_deg": round(az, 1),
                                "track": track,
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

    @staticmethod
    def _parse_norad_id(line1: str) -> int | None:
        """Catalog number from TLE line 1 (columns 3-7). Selection is by number, not name,
        because CelesTrak names vary ("ISS (ZARYA)", "HST", "CSS (TIANHE)")."""
        try:
            return int(line1[2:7])
        except (ValueError, IndexError):
            return None

    def get_orbits(self, now: datetime) -> list[dict[str, Any]]:
        """One full-period ground track + current sub-point per configured satellite (ADR-0041).

        No ephemeris needed: the sub-satellite point comes from the satellite's own geocentric
        position, not from the Sun/Earth ephemeris the pass-visibility check uses.
        """
        ts = load.timescale(builtin=True)
        tles = self._get_tles(self.config.orbit_tle_url, self.config.orbit_tle_cache_path)

        # Index the feed by NORAD id so we can select the configured set. First name wins on
        # the rare duplicate.
        by_norad: dict[int, tuple[str, str, str]] = {}
        for name, (line1, line2) in tles.items():
            norad = self._parse_norad_id(line1)
            if norad is not None and norad not in by_norad:
                by_norad[norad] = (name, line1, line2)

        samples = max(8, self.config.orbit_samples)
        orbits: list[dict[str, Any]] = []
        for norad in self.config.orbit_norad_ids:
            entry = by_norad.get(norad)
            if entry is None:
                logger.warning(f"Orbit satellite {norad} not in TLE feed; skipping.")
                continue
            name, line1, line2 = entry
            satellite = EarthSatellite(line1, line2, name, ts)

            # Mean motion (rad/min) → period (s). no_kozai is the SGP4 model's mean motion.
            no_kozai = float(getattr(satellite.model, "no_kozai", 0.0) or 0.0)
            if no_kozai <= 0.0:
                logger.warning(f"Orbit satellite {norad} has no usable mean motion; skipping.")
                continue
            period_s = (2.0 * math.pi / no_kozai) * 60.0

            track: list[dict[str, Any]] = []
            for index in range(samples + 1):
                when = now + timedelta(seconds=period_s * index / samples)
                subpoint = wgs84.subpoint(satellite.at(ts.from_datetime(when)))
                track.append(
                    {
                        "at": when,
                        "lat": round(subpoint.latitude.degrees, 3),
                        "lng": round(subpoint.longitude.degrees, 3),
                    }
                )
            orbits.append(
                {
                    "name": name,
                    "norad_id": norad,
                    # The current sub-point is the first sample (index 0 == now).
                    "sub_lat": track[0]["lat"],
                    "sub_lng": track[0]["lng"],
                    "track": track,
                }
            )
        return orbits


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

    # (name, norad_id, inclination_deg, ascending-node longitude, period_min). A spread of
    # inclinations so the mock globe shows visibly different loops, and the marquee ids the
    # real provider tracks so mock mode looks like production, not a different feature.
    _MOCK_ORBITS = (
        ("ISS (ZARYA)", 25544, 51.6, -45.0, 92.9),
        ("CSS (TIANHE)", 48274, 41.5, 100.0, 91.0),
        ("HST", 20580, 28.5, 10.0, 95.4),
        ("NOAA 15", 25338, 98.7, -120.0, 101.0),
    )
    _MOCK_SAMPLES = 90
    # Earth's rotation period in minutes (sidereal day), for the ground-track westward drift.
    _SIDEREAL_MIN = 1436.0

    def get_orbits(self, now: datetime) -> list[dict[str, Any]]:
        """Deterministic fake orbits so CI/dev works offline (ADR-0041, hardware-optional rule).

        Each is an inclined great circle drifting westward as Earth turns beneath it — the
        shape of a real ground track — computed from ``now`` alone, so a fixed ``now`` gives a
        fixed result the tests can assert on.
        """
        orbits: list[dict[str, Any]] = []
        for name, norad, incl_deg, node_lng, period_min in self._MOCK_ORBITS:
            incl = math.radians(incl_deg)
            track: list[dict[str, Any]] = []
            for index in range(self._MOCK_SAMPLES + 1):
                frac = index / self._MOCK_SAMPLES
                u = 2.0 * math.pi * frac  # argument of latitude, once around
                lat = math.degrees(math.asin(math.sin(incl) * math.sin(u)))
                lon_orbit = math.degrees(math.atan2(math.cos(incl) * math.sin(u), math.cos(u)))
                # Earth turns under the orbit over the elapsed fraction of a period.
                rotation = 360.0 * frac * (period_min / self._SIDEREAL_MIN)
                lng = ((node_lng + lon_orbit - rotation + 180.0) % 360.0) - 180.0
                when = now + timedelta(minutes=period_min * frac)
                track.append({"at": when, "lat": round(lat, 3), "lng": round(lng, 3)})
            orbits.append(
                {
                    "name": name,
                    "norad_id": norad,
                    "sub_lat": track[0]["lat"],
                    "sub_lng": track[0]["lng"],
                    "track": track,
                }
            )
        return orbits
