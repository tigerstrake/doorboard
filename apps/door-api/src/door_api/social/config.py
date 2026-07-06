"""Typed configuration for social feature limits and durations.

All rate limits are "N per window_s". Defaults match the T-403 brief
(5 writes/min/IP). Override via environment variables prefixed with
``DOOR_API_SOCIAL_``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return float(raw)


@dataclass(frozen=True, kw_only=True)
class SocialConfig:
    """Limits and durations for guestbook/poll/checkin public writes."""

    # SQLite database path for social tables.
    db_path: str

    # Rate limit for public writes (guestbook create, poll vote, checkin create,
    # deletion request): N writes per window_s, enforced per-IP and per-session-token.
    write_rate_limit_count: int = 5
    write_rate_limit_window_s: float = 60.0

    # Length caps.
    guestbook_text_max_len: int = 280
    guestbook_author_label_max_len: int = 40
    checkin_label_max_len: int = 40
    poll_question_max_len: int = 200
    poll_option_max_len: int = 80

    # List pagination default/max limits.
    default_list_limit: int = 20
    max_list_limit: int = 100

    # Placeholder admin gate: shared-secret bearer token compared with
    # secrets.compare_digest. This is NOT the packages/auth session/token
    # mechanism (that package is unbuilt as of this task) — see PR description
    # / escalation issue for the follow-up to replace this with real admin auth.
    admin_token: str | None = None

    @staticmethod
    def from_env() -> SocialConfig:
        db_path = os.environ.get("DOOR_API_SOCIAL_DB_PATH")
        if not db_path:
            ssd_root = os.environ.get("SSD_DATA_ROOT")
            if not ssd_root:
                raise RuntimeError(
                    "Either DOOR_API_SOCIAL_DB_PATH or SSD_DATA_ROOT must be set"
                )
            db_path = os.path.join(ssd_root, "door-api", "social.sqlite")

        return SocialConfig(
            db_path=db_path,
            write_rate_limit_count=_env_int("DOOR_API_SOCIAL_RATE_LIMIT_COUNT", 5),
            write_rate_limit_window_s=_env_float("DOOR_API_SOCIAL_RATE_LIMIT_WINDOW_S", 60.0),
            guestbook_text_max_len=_env_int("DOOR_API_SOCIAL_GUESTBOOK_TEXT_MAX_LEN", 280),
            guestbook_author_label_max_len=_env_int(
                "DOOR_API_SOCIAL_GUESTBOOK_AUTHOR_MAX_LEN", 40
            ),
            checkin_label_max_len=_env_int("DOOR_API_SOCIAL_CHECKIN_LABEL_MAX_LEN", 40),
            poll_question_max_len=_env_int("DOOR_API_SOCIAL_POLL_QUESTION_MAX_LEN", 200),
            poll_option_max_len=_env_int("DOOR_API_SOCIAL_POLL_OPTION_MAX_LEN", 80),
            default_list_limit=_env_int("DOOR_API_SOCIAL_DEFAULT_LIST_LIMIT", 20),
            max_list_limit=_env_int("DOOR_API_SOCIAL_MAX_LIST_LIMIT", 100),
            admin_token=os.environ.get("DOOR_API_SOCIAL_ADMIN_TOKEN"),
        )
