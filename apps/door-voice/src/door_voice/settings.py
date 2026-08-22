"""door-voice configuration (ADR-0034).

Everything defaults to the quiet, disabled reading: a misconfigured or
half-configured door says nothing rather than announcing names.
"""

from __future__ import annotations

from datetime import time as dtime
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from door_voice.policy import parse_quiet_hours


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # Off by default. ADR-0034: enrolment consent covers the doorpad screen, not
    # the shared corridor a speaker reaches.
    enabled: bool = Field(default=False, alias="FEATURE_VOICE_GREETING")

    # Opt-in ALLOW list, never a deny list: with a deny list the default for a
    # newly enrolled person is "announced", and forgetting someone discloses.
    # NoDecode because pydantic-settings would otherwise JSON-decode a plain
    # comma-separated string and fail on an empty value (the crash-loop that
    # took the NUC down twice on 2026-08-06).
    allow_person_ids_raw: Annotated[str, NoDecode] = Field(default="", alias="VOICE_GREETING_ALLOW")

    quiet_hours_raw: str = Field(default="22:00-08:00", alias="VOICE_GREETING_QUIET_HOURS")

    # Deliberately much longer than the visual greeting's 30 s: re-showing a name
    # is free, re-announcing it to a corridor is not.
    cooldown_s: float = Field(default=600.0, alias="VOICE_GREETING_COOLDOWN_S")

    door_api_ws_url: str = Field(default="ws://127.0.0.1:8080/ws", alias="VOICE_DOOR_API_WS_URL")
    greeting_template: str = Field(default="Hi {name}", alias="VOICE_GREETING_TEMPLATE")

    # Synthesis is always local (ADR-0034): a cloud TTS call would put a
    # recognised person's name on a third party on every approach.
    piper_binary: str = Field(default="piper", alias="VOICE_PIPER_BINARY")
    piper_voice: str = Field(default="", alias="VOICE_PIPER_VOICE")
    espeak_binary: str = Field(default="espeak-ng", alias="VOICE_ESPEAK_BINARY")
    aplay_binary: str = Field(default="aplay", alias="VOICE_APLAY_BINARY")
    alsa_device: str = Field(default="", alias="VOICE_ALSA_DEVICE")

    # A wedged synthesiser must not accumulate processes behind it.
    speak_timeout_s: float = Field(default=8.0, alias="VOICE_SPEAK_TIMEOUT_S")
    reconnect_delay_s: float = Field(default=3.0, alias="VOICE_RECONNECT_DELAY_S")

    @field_validator("cooldown_s", "speak_timeout_s", "reconnect_delay_s")
    @classmethod
    def _must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be greater than 0")
        return value

    @property
    def allowed_person_ids(self) -> frozenset[str]:
        return frozenset(
            part.strip() for part in self.allow_person_ids_raw.split(",") if part.strip()
        )

    @property
    def quiet_hours(self) -> tuple[dtime, dtime] | None:
        return parse_quiet_hours(self.quiet_hours_raw)
