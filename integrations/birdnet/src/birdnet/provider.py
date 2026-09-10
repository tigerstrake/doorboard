from __future__ import annotations

import abc
import logging
from datetime import date, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger("doorboard.birdnet")


class BirdnetConfig(BaseModel):
    url: str = "http://127.0.0.1:8080"
    confidence_threshold: float = 0.70
    species_filter: list[str] = Field(default_factory=list)
    # The zone "today" is counted in. Empty means the process-local zone, which in the
    # container is `TZ` — the same source the academic countdown reads (ADR-0039). "Today"
    # is a local-calendar fact: on the UTC date, every evening's detections vanished from
    # ~17:00 local until midnight.
    tz: str = ""


class BirdDetection(BaseModel):
    common_name: str
    scientific_name: str
    confidence: float
    timestamp: datetime


class BirdProvider(abc.ABC):
    @abc.abstractmethod
    def get_summary(self, now: datetime) -> tuple[int, list[dict[str, Any]]]:
        """Fetch detections and return (total_detections, list of top_species summaries)."""
        pass


class BirdnetGoProvider(BirdProvider):
    def __init__(self, config: BirdnetConfig) -> None:
        self.config = config

    def _zone(self) -> tzinfo | None:
        return ZoneInfo(self.config.tz) if self.config.tz else None

    def _fetch_detections(self, today: date) -> list[dict[str, Any]]:
        """The detections BirdNET-Go holds for `today` (one local calendar day)."""
        url = f"{self.config.url.rstrip('/')}/api/v2/detections"
        params = {"start_date": today.isoformat(), "end_date": today.isoformat()}
        resp = httpx.get(url, params=params, timeout=5.0)
        if resp.status_code != 200:
            logger.error(f"BirdNET-Go returned status {resp.status_code}: {resp.text}")
            raise RuntimeError(f"BirdNET-Go error: {resp.status_code}")

        data = resp.json()
        if isinstance(data, dict):
            return data.get("detections") or data.get("results") or data.get("data") or []
        if isinstance(data, list):
            return data
        return []

    @staticmethod
    def _detection_time(d: dict[str, Any], zone: tzinfo | None) -> datetime | None:
        """When the detection happened, as an aware datetime.

        Prefers `timestamp`, which BirdNET-Go builds as RFC3339 *with* the bird Pi's zone.
        Falls back to the naive `date` (+ `time`) pair, which is stamped in that same local
        zone — so a naive value is attached to `zone`, never assumed to be UTC.
        """
        stamp = d.get("timestamp")
        if stamp:
            try:
                dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    return dt
            except ValueError:
                pass

        date_str = d.get("date")
        if not date_str:
            return None
        text = str(date_str).strip()
        time_str = d.get("time")
        if time_str and "T" not in text and " " not in text:
            text = f"{text}T{time_str}"
        try:
            dt = datetime.fromisoformat(text.replace(" ", "T"))
        except ValueError:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo is not None:
            return dt
        # An explicit zone is attached as-is; with none configured, the process-local zone
        # (the container's TZ) is what `astimezone()` on a naive value means.
        return dt.replace(tzinfo=zone) if zone is not None else dt.astimezone()

    def get_summary(self, now: datetime) -> tuple[int, list[dict[str, Any]]]:
        zone = self._zone()
        today = now.astimezone(zone).date()

        try:
            detections_raw = self._fetch_detections(today)
        except Exception as exc:
            logger.error(f"Failed to fetch from BirdNET-Go: {exc}")
            raise RuntimeError(f"Unreachable: {exc}") from exc

        # Process and filter detections
        filtered: list[BirdDetection] = []
        for d in detections_raw:
            try:
                common_name = d.get("commonName") or d.get("common_name")
                scientific_name = d.get("scientificName") or d.get("scientific_name")
                confidence = float(d.get("confidence", 0.0))

                dt = self._detection_time(d, zone)
                if not common_name or dt is None:
                    continue

                # Filter by date (ensure it's today, in the local calendar)
                if dt.astimezone(zone).date() != today:
                    continue

                # Filter by confidence
                if confidence < self.config.confidence_threshold:
                    continue

                # Filter by regional species list
                if self.config.species_filter and (
                    common_name not in self.config.species_filter
                    and scientific_name not in self.config.species_filter
                ):
                    continue

                filtered.append(
                    BirdDetection(
                        common_name=common_name,
                        scientific_name=scientific_name or "",
                        confidence=confidence,
                        timestamp=dt,
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to parse detection record: {d}, error: {e}")
                continue

        # Summarize top species
        species_stats: dict[str, list[float]] = {}
        for fd in filtered:
            species_stats.setdefault(fd.common_name, []).append(fd.confidence)

        top_species = []
        for name, confs in species_stats.items():
            top_species.append(
                {
                    "name": name,
                    "count": len(confs),
                    "confidence_avg": round(sum(confs) / len(confs), 2),
                }
            )

        # Sort by count desc, then confidence_avg desc
        top_species.sort(key=lambda x: (-x["count"], -x["confidence_avg"]))

        return len(filtered), top_species


class MockBirdProvider(BirdProvider):
    def get_summary(self, now: datetime) -> tuple[int, list[dict[str, Any]]]:
        # Realistic mock data matching the birdFixture in fixtures.ts
        total = 7
        top = [
            {"name": "House Finch", "count": 4, "confidence_avg": 0.88},
            {"name": "Mourning Dove", "count": 2, "confidence_avg": 0.79},
        ]
        return total, top
