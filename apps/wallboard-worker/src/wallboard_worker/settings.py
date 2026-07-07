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
