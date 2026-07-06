"""Typed configuration for door-api session timeouts and durations.

All durations are in seconds. Defaults match ARCHITECTURE.md §8 and the T-401 brief.
Override via environment variables prefixed with ``DOOR_API_``.
"""

from __future__ import annotations

import os
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

    # Greeting cooldown per person — architecture §5 specifies 30 s.
    greeting_cooldown_s: float = 30.0

    # SESSION_END lingers briefly before auto-transitioning to IDLE (seconds).
    session_end_linger_s: float = 3.0

    # SQLite database path. Must be provided explicitly or loaded via from_env().
    db_path: str

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
            greeting_cooldown_s=_env_float("DOOR_API_GREETING_COOLDOWN_S", 30.0),
            session_end_linger_s=_env_float("DOOR_API_SESSION_END_LINGER_S", 3.0),
            db_path=db_path,
        )
