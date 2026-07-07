"""Typed configuration for door-api session timeouts and durations.

All durations are in seconds. Defaults match ARCHITECTURE.md §8 and the T-401 brief.
Override via environment variables prefixed with ``DOOR_API_``.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return float(raw)


@dataclass(frozen=True, kw_only=True)
class SessionConfig:
    """Timeouts and durations for the visitor session state machine.

    Each value is documented with its purpose and default rationale.
    """

    # How long to wait for the bell to be answered before UNANSWERED_TIMEOUT (seconds).
    ring_timeout_s: float = 30.0

    # How long VISITOR_MODE persists without interaction before auto-transitioning
    # to RINGING (seconds). Immediate in most flows; this is the maximum.
    visitor_mode_auto_ring_s: float = 2.0

    # How long to wait in UNANSWERED_TIMEOUT / ANSWERED before offering video message (seconds).
    offer_delay_s: float = 3.0

    # Maximum recording duration for a video message (seconds).
    max_recording_s: float = 60.0

    # How long VIDEO_MESSAGE_REVIEW stays active before auto-SESSION_END (seconds).
    review_timeout_s: float = 30.0

    # How long VIDEO_MESSAGE_SAVED shows confirmation before SESSION_END (seconds).
    saved_linger_s: float = 5.0

    # Inactivity timeout: if no transition occurs within this many seconds,
    # the session auto-expires to IDLE regardless of current state (seconds).
    inactivity_timeout_s: float = 120.0

    # APPROACH_DETECTED / IDENTITY_CACHED expire back to IDLE after this long
    # with no button press (seconds).
    approach_timeout_s: float = 10.0

    # SESSION_END lingers briefly before auto-transitioning to IDLE (seconds).
    session_end_linger_s: float = 3.0

    # SQLite database path. Must be provided explicitly or loaded via from_env().
    db_path: str

    # Door identifier included on locally emitted feedback events.
    door_id: str = "primary"

    # door-media base URL used for fire-and-forget recording lifecycle forwarding.
    media_base_url: str = "http://127.0.0.1:8001"

    # Browser-reachable media URL for local DoorPad playback.
    media_public_base_url: str = "http://127.0.0.1:8001"

    # Bounded timeout for door-api -> door-media local loopback calls.
    media_timeout_s: float = 1.0

    # Short-lived visitor QR tokens.  If unset, a per-process boot secret is used.
    visitor_token_secret: str = ""
    visitor_token_ttl_s: float = 300.0
    visitor_public_base_url: str = "http://door.local"

    # ESP32 feedback effect requested for DoorPad touch actions.
    doorpad_effect_id: str = "generic_chime"
    doorpad_effect_duration_ms: int = 900

    @staticmethod
    def from_env() -> SessionConfig:
        """Load configuration, applying environment variable overrides."""
        db_path = os.environ.get("DOOR_API_DB_PATH")
        if not db_path:
            ssd_root = os.environ.get("SSD_DATA_ROOT")
            if not ssd_root:
                raise RuntimeError("Either DOOR_API_DB_PATH or SSD_DATA_ROOT must be set")
            db_path = os.path.join(ssd_root, "door-api", "session.sqlite")

        return SessionConfig(
            ring_timeout_s=_env_float("DOOR_API_RING_TIMEOUT_S", 30.0),
            visitor_mode_auto_ring_s=_env_float("DOOR_API_VISITOR_MODE_AUTO_RING_S", 2.0),
            offer_delay_s=_env_float("DOOR_API_OFFER_DELAY_S", 3.0),
            max_recording_s=_env_float("DOOR_API_MAX_RECORDING_S", 60.0),
            review_timeout_s=_env_float("DOOR_API_REVIEW_TIMEOUT_S", 30.0),
            saved_linger_s=_env_float("DOOR_API_SAVED_LINGER_S", 5.0),
            inactivity_timeout_s=_env_float("DOOR_API_INACTIVITY_TIMEOUT_S", 120.0),
            approach_timeout_s=_env_float("DOOR_API_APPROACH_TIMEOUT_S", 10.0),
            session_end_linger_s=_env_float("DOOR_API_SESSION_END_LINGER_S", 3.0),
            db_path=db_path,
            door_id=os.environ.get("DOOR_API_DOOR_ID", "primary"),
            media_base_url=os.environ.get("DOOR_API_MEDIA_BASE_URL", "http://127.0.0.1:8001"),
            media_public_base_url=os.environ.get(
                "DOOR_API_MEDIA_PUBLIC_BASE_URL",
                os.environ.get("DOOR_API_MEDIA_BASE_URL", "http://127.0.0.1:8001"),
            ),
            media_timeout_s=_env_float("DOOR_API_MEDIA_TIMEOUT_S", 1.0),
            visitor_token_secret=os.environ.get(
                "DOOR_API_VISITOR_TOKEN_SECRET",
                secrets.token_urlsafe(32),
            ),
            visitor_token_ttl_s=_env_float("DOOR_API_VISITOR_TOKEN_TTL_S", 300.0),
            visitor_public_base_url=os.environ.get(
                "DOOR_API_VISITOR_PUBLIC_BASE_URL",
                "http://door.local",
            ),
            doorpad_effect_id=os.environ.get("DOOR_API_DOORPAD_EFFECT_ID", "generic_chime"),
            doorpad_effect_duration_ms=int(
                _env_float("DOOR_API_DOORPAD_EFFECT_DURATION_MS", 900.0)
            ),
        )
