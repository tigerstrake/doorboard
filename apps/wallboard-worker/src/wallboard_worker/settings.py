from __future__ import annotations

from pydantic import Field
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

    feature_aircraft: bool = Field(default=False, alias="FEATURE_AIRCRAFT")
    opensky_username: str = Field(default="", alias="OPENSKY_USERNAME")
    opensky_password: str = Field(default="", alias="OPENSKY_PASSWORD")
    aircraft_observer_lat: float = Field(default=0.0, alias="AIRCRAFT_OBSERVER_LAT")
    aircraft_observer_lon: float = Field(default=0.0, alias="AIRCRAFT_OBSERVER_LON")
    aircraft_bbox_half_size_lat: float = Field(default=0.25, alias="AIRCRAFT_BBOX_HALF_SIZE_LAT")
    aircraft_bbox_half_size_lon: float = Field(default=0.25, alias="AIRCRAFT_BBOX_HALF_SIZE_LON")
    aircraft_poll_cooldown_seconds: int = Field(default=30, alias="AIRCRAFT_POLL_COOLDOWN_SECONDS")
