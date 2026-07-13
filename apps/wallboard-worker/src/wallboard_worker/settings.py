from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    door_id: str = Field(default="primary", alias="DOORBOARD_DOOR_ID")
    control_plane_url: str = Field(default="http://127.0.0.1:8090", alias="CONTROL_PLANE_URL")
    control_plane_admin_token: str = Field(default="", alias="CONTROL_PLANE_ADMIN_TOKEN")
    ingest_token: str = Field(default="", alias="WALLBOARD_WORKER_INGEST_TOKEN")
    scheduler_heartbeat_path: Path = Field(
        default=Path("/tmp/wallboard-worker-heartbeat"),
        alias="WALLBOARD_WORKER_HEARTBEAT_PATH",
    )

    bird_summary_interval_s: float = Field(default=300.0, alias="WALLBOARD_BIRD_INTERVAL_S", gt=0)
    satellite_interval_s: float = Field(
        default=3600.0, alias="WALLBOARD_SATELLITE_INTERVAL_S", gt=0
    )
    aircraft_interval_s: float = Field(default=30.0, alias="WALLBOARD_AIRCRAFT_INTERVAL_S", gt=0)
    printer_interval_s: float = Field(default=30.0, alias="WALLBOARD_PRINTER_INTERVAL_S", gt=0)
    food_interval_s: float = Field(default=86400.0, alias="WALLBOARD_FOOD_INTERVAL_S", gt=0)
    collage_interval_s: float = Field(default=86400.0, alias="WALLBOARD_COLLAGE_INTERVAL_S", gt=0)

    feature_birdnet: bool = Field(default=False, alias="FEATURE_BIRDNET")
    bird_provider: Literal["birdnet_go", "avian_visitors", "mock"] = Field(
        default="birdnet_go", alias="BIRD_PROVIDER"
    )
    birdnet_url: str = Field(default="http://127.0.0.1:8080", alias="BIRDNET_URL")
    birdnet_confidence_threshold: float = Field(default=0.70, alias="BIRDNET_CONFIDENCE_THRESHOLD")
    birdnet_species_filter: list[str] = Field(default_factory=list, alias="BIRDNET_SPECIES_FILTER")
    avian_visitors_url: str = Field(default="http://birdnet.local", alias="AVIAN_VISITORS_URL")
    avian_visitors_recent_hours: int = Field(
        default=24, alias="AVIAN_VISITORS_RECENT_HOURS", ge=1, le=168
    )
    avian_visitors_basic_user: str = Field(default="", alias="AVIAN_VISITORS_BASIC_USER")
    avian_visitors_basic_password: SecretStr = Field(
        default_factory=lambda: SecretStr(""), alias="AVIAN_VISITORS_BASIC_PASSWORD"
    )
    avian_visitors_timeout_s: float = Field(
        default=5.0, alias="AVIAN_VISITORS_TIMEOUT_S", gt=0, le=30
    )

    feature_satellites: bool = Field(default=False, alias="FEATURE_SATELLITES")
    satellites_watchlist: list[str] = Field(
        default_factory=lambda: ["ISS (ZARYA)"], alias="SATELLITES_WATCHLIST"
    )
    satellites_observer_lat: float = Field(default=0.0, alias="SATELLITES_OBSERVER_LAT")
    satellites_observer_lon: float = Field(default=0.0, alias="SATELLITES_OBSERVER_LON")
    satellites_observer_elevation: float = Field(default=0.0, alias="SATELLITES_OBSERVER_ELEVATION")
    satellites_min_elevation: float = Field(default=10.0, alias="SATELLITES_MIN_ELEVATION")
    satellites_tle_url: str = Field(
        default="https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle",
        alias="SATELLITES_TLE_URL",
    )
    satellites_tle_cache_path: str = Field(
        default="/tmp/satellite_tle_cache.txt",
        alias="SATELLITES_TLE_CACHE_PATH",
    )

    feature_aircraft: bool = Field(default=False, alias="FEATURE_AIRCRAFT")
    opensky_username: str = Field(default="", alias="OPENSKY_USERNAME")
    opensky_password: str = Field(default="", alias="OPENSKY_PASSWORD")
    aircraft_observer_lat: float = Field(default=0.0, alias="AIRCRAFT_OBSERVER_LAT")
    aircraft_observer_lon: float = Field(default=0.0, alias="AIRCRAFT_OBSERVER_LON")
    aircraft_bbox_half_size_lat: float = Field(default=0.25, alias="AIRCRAFT_BBOX_HALF_SIZE_LAT")
    aircraft_bbox_half_size_lon: float = Field(default=0.25, alias="AIRCRAFT_BBOX_HALF_SIZE_LON")
    aircraft_poll_cooldown_seconds: int = Field(default=30, alias="AIRCRAFT_POLL_COOLDOWN_SECONDS")

    feature_printer: bool = Field(default=False, alias="FEATURE_PRINTER")
    octoprint_url: str = Field(default="http://octopi.local", alias="OCTOPRINT_URL")
    octoprint_api_key: str = Field(default="", alias="OCTOPRINT_API_KEY")
    printer_camera_stream_url: str = Field(default="", alias="PRINTER_CAMERA_STREAM_URL")

    feature_food: bool = Field(default=False, alias="FEATURE_FOOD")
    food_cache_path: str = Field(
        default="/tmp/doorboard_food_recommendation_cache.json",
        alias="FOOD_RECOMMENDATION_CACHE_PATH",
    )

    @field_validator("birdnet_species_filter", mode="before")
    @classmethod
    def parse_species(cls, v: object) -> list[str]:
        if isinstance(v, str):
            if not v.strip():
                return []
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(item) for item in v]
        return []

    @field_validator("satellites_watchlist", mode="before")
    @classmethod
    def parse_watchlist(cls, v: object) -> list[str]:
        if isinstance(v, str):
            if not v.strip():
                return ["ISS (ZARYA)"]
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(item) for item in v]
        return ["ISS (ZARYA)"]

    @model_validator(mode="after")
    def validate_worker_configuration(self) -> Settings:
        password = self.avian_visitors_basic_password.get_secret_value()
        if bool(self.avian_visitors_basic_user) != bool(password):
            raise ValueError("AvianVisitors basic auth requires both user and password")

        enabled = any(
            (
                self.feature_birdnet,
                self.feature_satellites,
                self.feature_aircraft,
                self.feature_printer,
                self.feature_food,
            )
        )
        if enabled and not (self.ingest_token or self.control_plane_admin_token):
            raise ValueError("enabled wallboard jobs require WALLBOARD_WORKER_INGEST_TOKEN")
        return self
