from __future__ import annotations

import abc
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger("doorboard.birdnet")


class BirdnetConfig(BaseModel):
    url: str = "http://127.0.0.1:8080"
    confidence_threshold: float = 0.70
    species_filter: list[str] = Field(default_factory=list)


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

    def get_summary(self, now: datetime) -> tuple[int, list[dict[str, Any]]]:
        # Poll BirdNET-Go API v2
        url = f"{self.config.url.rstrip('/')}/api/v2/detections"
        try:
            # We filter for today's detections.
            # In BirdNET-Go, we can pass start_date in YYYY-MM-DD
            today_str = now.strftime("%Y-%m-%d")
            params = {"start_date": today_str}
            resp = httpx.get(url, params=params, timeout=5.0)
            if resp.status_code != 200:
                logger.error(f"BirdNET-Go returned status {resp.status_code}: {resp.text}")
                raise RuntimeError(f"BirdNET-Go error: {resp.status_code}")

            data = resp.json()
            if isinstance(data, dict):
                detections_raw = (
                    data.get("detections") or data.get("results") or data.get("data") or []
                )
            elif isinstance(data, list):
                detections_raw = data
            else:
                detections_raw = []

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
                date_str = d.get("date")

                if not common_name or not date_str:
                    continue

                # Parse date (format is typically YYYY-MM-DD HH:MM:SS or ISO format)
                try:
                    dt = datetime.fromisoformat(date_str.replace(" ", "T"))
                except ValueError:
                    # Try parsing common Go date formats
                    dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

                # Set UTC timezone if naive
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)

                # Filter by date (ensure it's today)
                if dt.date() != now.date():
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
