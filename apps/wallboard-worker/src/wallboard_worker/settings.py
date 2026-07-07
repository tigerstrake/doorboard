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

    feature_printer: bool = Field(default=False, alias="FEATURE_PRINTER")
    octoprint_url: str = Field(default="http://octopi.local", alias="OCTOPRINT_URL")
    octoprint_api_key: str = Field(default="", alias="OCTOPRINT_API_KEY")
    printer_camera_stream_url: str = Field(default="", alias="PRINTER_CAMERA_STREAM_URL")
