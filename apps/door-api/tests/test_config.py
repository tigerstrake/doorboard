"""Regression tests for door-api SessionConfig defaults.

Guards two default-value bugs found in audit:
- review_timeout_s must be >= max_recording_s, else a full-length video message
  is auto-discarded mid-review (the timer is a fixed countdown from entering
  REVIEW and is not paused by playback).
- media_base_url / media_public_base_url must point at door-media's real port
  (8082), not the stale 8001 default that silently broke media forwarding.
"""

from __future__ import annotations

import logging

import pytest
from door_api.config import SessionConfig


def _clear_timeout_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    for name in (
        "DOOR_API_REVIEW_TIMEOUT_S",
        "DOOR_API_MAX_RECORDING_S",
        "DOOR_API_MEDIA_BASE_URL",
        "DOOR_API_MEDIA_PUBLIC_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_review_timeout_covers_a_full_length_recording() -> None:
    cfg = SessionConfig(db_path=":memory:")
    assert cfg.review_timeout_s >= cfg.max_recording_s, (
        "review window must fit watching + saving a max-length message"
    )


def test_from_env_default_review_timeout_covers_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_timeout_env(monkeypatch)
    cfg = SessionConfig.from_env()
    assert cfg.review_timeout_s >= cfg.max_recording_s


def test_from_env_warns_when_review_timeout_below_recording(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _clear_timeout_env(monkeypatch)
    monkeypatch.setenv("DOOR_API_REVIEW_TIMEOUT_S", "10")
    monkeypatch.setenv("DOOR_API_MAX_RECORDING_S", "60")
    with caplog.at_level(logging.WARNING, logger="door_api.config"):
        cfg = SessionConfig.from_env()
    assert cfg.review_timeout_s == 10.0  # explicit override is honored...
    assert any("REVIEW_TIMEOUT" in r.message for r in caplog.records)  # ...but warned


def test_media_base_url_defaults_to_door_media_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_timeout_env(monkeypatch)
    cfg = SessionConfig.from_env()
    assert cfg.media_base_url == "http://127.0.0.1:8082"
    assert cfg.media_public_base_url == "http://127.0.0.1:8082"
    # And the dataclass field default matches (not the stale :8001).
    assert SessionConfig(db_path=":memory:").media_base_url == "http://127.0.0.1:8082"


def test_the_visitor_writing_window_is_not_shorter_than_the_doorpad_promises() -> None:
    """The server must not cut off a message the doorboard gave time to write.

    The DoorPad allows 10 minutes on the "scan to leave a message" screen
    (VISITOR_WRITING_TIMEOUT_MS in door-ui/src/doorpadTimeouts.ts). Whichever side is
    shorter is the real limit, and this side was 120s — so a visitor was cut off two
    and a half minutes into a note. Asserted rather than commented because the two
    numbers live in different languages and nothing else relates them.
    """
    config = SessionConfig(db_path=":memory:")
    doorpad_writing_window_s = 600.0
    assert config.inactivity_timeout_s >= doorpad_writing_window_s

    # And the visitor token has to outlast neither: it is refreshed by the page, but
    # a token TTL longer than the session window would promise a link the session
    # cannot honour.
    assert config.visitor_token_ttl_s <= config.inactivity_timeout_s
