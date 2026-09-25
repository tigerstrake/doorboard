"""door-api reads the documented DOORBOARD_DOOR_ID, with DOOR_API_DOOR_ID as a one-release fallback.

Every other Pi service reads ``DOORBOARD_DOOR_ID`` (the name in both env templates);
door-api alone read ``DOOR_API_DOOR_ID``. A Pi configured the documented way therefore
had door-api stamping ``door_id="primary"`` on session and social events while visiond,
media and sync stamped the configured id — latent today (v1 is ``primary`` only), wrong
the day a second door exists.
"""

from __future__ import annotations

import logging

import pytest
from door_api.config import SessionConfig, door_id_from_env
from door_api.social.config import SocialConfig


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.delenv("DOORBOARD_DOOR_ID", raising=False)
    monkeypatch.delenv("DOOR_API_DOOR_ID", raising=False)


def test_the_documented_name_reaches_both_configs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOORBOARD_DOOR_ID", "garage")
    assert SessionConfig.from_env().door_id == "garage"
    assert SocialConfig.from_env().door_id == "garage"


def test_the_legacy_name_still_works_and_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DOOR_API_DOOR_ID", "legacy")
    with caplog.at_level(logging.WARNING, logger="door_api.config"):
        assert door_id_from_env() == "legacy"
    assert any("DOOR_API_DOOR_ID is deprecated" in r.getMessage() for r in caplog.records), (
        "an operator on the old spelling must be told to rename it before the fallback goes"
    )
    assert SessionConfig.from_env().door_id == "legacy"
    assert SocialConfig.from_env().door_id == "legacy"


def test_the_documented_name_wins_when_both_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOORBOARD_DOOR_ID", "documented")
    monkeypatch.setenv("DOOR_API_DOOR_ID", "legacy")
    assert door_id_from_env() == "documented"


def test_neither_set_falls_back_to_primary() -> None:
    assert door_id_from_env() == "primary"
    assert SessionConfig.from_env().door_id == "primary"


def test_a_blank_value_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # An EnvironmentFile line left as `DOORBOARD_DOOR_ID=` must not stamp an empty id.
    monkeypatch.setenv("DOORBOARD_DOOR_ID", "   ")
    assert door_id_from_env() == "primary"
