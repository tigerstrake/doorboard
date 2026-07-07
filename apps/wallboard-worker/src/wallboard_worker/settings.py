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

    feature_food: bool = Field(default=False, alias="FEATURE_FOOD")
    food_provider_type: str = Field(default="mock", alias="FOOD_PROVIDER_TYPE")
