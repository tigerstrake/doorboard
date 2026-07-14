from __future__ import annotations

from pydantic import Field, field_validator
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

    feature_birdnet: bool = Field(default=False, alias="FEATURE_BIRDNET")
    birdnet_url: str = Field(default="http://127.0.0.1:8080", alias="BIRDNET_URL")
    birdnet_confidence_threshold: float = Field(default=0.70, alias="BIRDNET_CONFIDENCE_THRESHOLD")
    birdnet_species_filter: list[str] = Field(default_factory=list, alias="BIRDNET_SPECIES_FILTER")

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
    # "mock" (default) or "stanford" for the real Stanford R&DE dining provider.
    food_provider: str = Field(default="mock", alias="FOOD_PROVIDER")
    # Comma-separated hall filter (e.g. "Wilbur,Stern"); empty = all halls. Kept
    # as a plain string because pydantic-settings JSON-decodes list-typed env
    # vars before validators run; the CLI splits it.
    food_hall_ids: str = Field(default="", alias="FOOD_HALL_IDS")
    food_meal_override: str = Field(default="", alias="FOOD_MEAL_OVERRIDE")
    food_preferences_path: str = Field(default="", alias="FOOD_PREFERENCES_PATH")
    # LLM refinement is opt-in and only used when an API key is present.
    food_use_ai: bool = Field(default=False, alias="FOOD_USE_AI")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    def food_hall_id_list(self) -> list[str]:
        return [h.strip() for h in self.food_hall_ids.split(",") if h.strip()]

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
