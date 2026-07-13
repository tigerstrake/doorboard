from __future__ import annotations

import abc
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

logger = logging.getLogger("doorboard.birdnet")


class BirdnetConfig(BaseModel):
    url: str = "http://127.0.0.1:8080"
    confidence_threshold: float = 0.70
    species_filter: list[str] = Field(default_factory=list)


class AvianVisitorsConfig(BaseModel):
    """Connection settings for Twarner491/AvianVisitors' read-only API."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(default="http://birdnet.local", min_length=1, max_length=2048)
    confidence_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    species_filter: list[str] = Field(default_factory=list)
    recent_hours: int = Field(default=24, ge=1, le=168)
    basic_user: str = ""
    basic_password: SecretStr = Field(default_factory=lambda: SecretStr(""))
    timeout_s: float = Field(default=5.0, gt=0.0, le=30.0)
    max_response_bytes: int = Field(default=2_000_000, ge=1024, le=10_000_000)
    max_species_rows: int = Field(default=512, ge=1, le=4096)

    @field_validator("url")
    @classmethod
    def url_is_lan_http_base(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("AvianVisitors URL must be an HTTP(S) base URL")
        if parsed.username or parsed.password:
            raise ValueError("AvianVisitors URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("AvianVisitors URL must not contain a query or fragment")
        return value

    @model_validator(mode="after")
    def auth_is_complete(self) -> AvianVisitorsConfig:
        password = self.basic_password.get_secret_value()
        if bool(self.basic_user) != bool(password):
            raise ValueError("AvianVisitors basic auth requires both user and password")
        return self


class _AvianSpeciesRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sci: str = Field(min_length=1, max_length=200)
    com: str = Field(min_length=1, max_length=200)
    n: int = Field(ge=1, le=1_000_000_000, strict=True)
    best_conf: float = Field(ge=0.0, le=1.0)
    last_seen: datetime

    @field_validator("best_conf", mode="before")
    @classmethod
    def confidence_is_a_json_number(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("AvianVisitors best_conf must be a JSON number")
        return value

    @field_validator("last_seen", mode="before")
    @classmethod
    def last_seen_uses_the_native_timestamp_format(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("AvianVisitors last_seen must be a timestamp string")
        try:
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise ValueError("AvianVisitors last_seen has an invalid format") from exc
        return value


class _AvianRecentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hours: int = Field(ge=1, le=1_000_000, strict=True)
    species: list[_AvianSpeciesRow]
    as_of: datetime

    @model_validator(mode="after")
    def metadata_is_consistent(self) -> _AvianRecentResponse:
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("AvianVisitors as_of must include a UTC offset")
        scientific_names: set[str] = set()
        for row in self.species:
            key = row.sci.casefold()
            if key in scientific_names:
                raise ValueError("AvianVisitors response contains a duplicate species")
            scientific_names.add(key)
        return self


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


class AvianVisitorsProvider(BirdProvider):
    """Consume the native AvianVisitors recent-species API.

    AvianVisitors returns one row per species with a count and the best
    confidence in the requested window. The existing Doorboard contract calls
    its confidence statistic ``confidence_avg``; until upstream exposes an
    average, the best confidence is the only truthful aggregate available and
    is carried in that field.
    """

    def __init__(
        self,
        config: AvianVisitorsConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport

    def get_summary(self, now: datetime) -> tuple[int, list[dict[str, Any]]]:
        del now  # AvianVisitors applies its own local clock to the rolling window.
        url = f"{self.config.url.rstrip('/')}/avian/api/birdnet-api.php"
        password = self.config.basic_password.get_secret_value()
        auth = (
            httpx.BasicAuth(self.config.basic_user, password)
            if self.config.basic_user and password
            else None
        )
        try:
            with (
                httpx.Client(
                    auth=auth,
                    follow_redirects=False,
                    timeout=self.config.timeout_s,
                    transport=self._transport,
                ) as client,
                client.stream(
                    "GET",
                    url,
                    params={"action": "recent", "hours": self.config.recent_hours},
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "Doorboard-AvianVisitors/1.0",
                    },
                ) as response,
            ):
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self.config.max_response_bytes:
                        raise ValueError("response exceeds configured size limit")
            parsed = _AvianRecentResponse.model_validate_json(body)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "avian_visitors_fetch_failed",
                extra={"error_type": type(exc).__name__},
            )
            raise RuntimeError(f"AvianVisitors unavailable: {type(exc).__name__}") from exc

        if parsed.hours != self.config.recent_hours:
            raise RuntimeError("AvianVisitors response window does not match the request")
        if len(parsed.species) > self.config.max_species_rows:
            raise RuntimeError("AvianVisitors response has too many species rows")

        allowed = {item.casefold() for item in self.config.species_filter}
        top_species: list[dict[str, Any]] = []
        total = 0
        for row in parsed.species:
            if row.best_conf < self.config.confidence_threshold:
                continue
            if allowed and row.com.casefold() not in allowed and row.sci.casefold() not in allowed:
                continue
            total += row.n
            top_species.append(
                {
                    "name": row.com,
                    "count": row.n,
                    "confidence_avg": round(row.best_conf, 2),
                }
            )

        top_species.sort(key=lambda item: (-item["count"], -item["confidence_avg"], item["name"]))
        return total, top_species


class MockBirdProvider(BirdProvider):
    def get_summary(self, now: datetime) -> tuple[int, list[dict[str, Any]]]:
        # Realistic mock data matching the birdFixture in fixtures.ts
        total = 7
        top = [
            {"name": "House Finch", "count": 4, "confidence_avg": 0.88},
            {"name": "Mourning Dove", "count": 2, "confidence_avg": 0.79},
        ]
        return total, top
