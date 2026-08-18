"""The held identity must outlast door-visiond's greeting cooldown.

The bug this pins, measured on the door. While a person stands there continuously,
`vision.identity_stable` arrives at exactly the cooldown floor:

    20:21:36   20:22:06   20:22:36     (30 s apart, to the second)

door-api's idle hold was 12 s. So the name expired 18 seconds before the next announcement
could possibly arrive, and for 18 of every 30 seconds the door had forgotten someone standing
in front of it — with no way for them to fix it, because looking at the camera again produces
nothing the cooldown will let through.

The cooldown exists so the door does not greet you every two seconds (P-10). It was never
meant to mean "door-api may forget you". With no separate liveness signal on the wire, this
holder's window has to cover it, and these tests make the coupling explicit so the two
constants cannot drift back into a dead zone.
"""

from __future__ import annotations

import pytest
from door_api.config import SessionConfig
from door_api.recognised_identity import RecognisedIdentity


@pytest.fixture(autouse=True)
def _db_path(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # SessionConfig.from_env needs a DB path; the values under test are unrelated to it.
    monkeypatch.setenv("DOOR_API_DB_PATH", str(tmp_path / "session.sqlite"))


# door-visiond's VISIOND_GREETING_COOLDOWN_MS default, in seconds. Duplicated deliberately:
# the two services do not share a settings module, and the point of this file is to fail if
# they diverge rather than to follow one silently.
VISIOND_GREETING_COOLDOWN_S = 30.0


def test_the_idle_window_outlasts_the_greeting_cooldown() -> None:
    config = SessionConfig.from_env()
    assert config.recognised_identity_idle_ttl_s >= VISIOND_GREETING_COOLDOWN_S, (
        "the held identity expires before door-visiond is allowed to re-announce the person, "
        "so the door forgets someone who is still standing there"
    )


def test_a_still_present_person_survives_a_whole_cooldown_gap() -> None:
    """The concrete scenario: recognised once, then nothing until the cooldown lapses."""
    clock = {"t": 1000.0}
    config = SessionConfig.from_env()
    identity = RecognisedIdentity(
        idle_ttl_s=config.recognised_identity_idle_ttl_s,
        interaction_ttl_s=config.recognised_identity_interaction_ttl_s,
        monotonic_fn=lambda: clock["t"],
    )
    identity.remember(
        person_id="prs_tiger",
        display_name="Tiger",
        consent_version="v3",
        profile_id="warm_amber",
    )

    # One second before the next announcement is even permitted, they must still be known.
    clock["t"] += VISIOND_GREETING_COOLDOWN_S - 1
    held = identity.current()
    assert held is not None, "forgotten mid-cooldown, while still at the door"
    assert held.display_name == "Tiger"


def test_it_still_forgets_someone_who_has_actually_left() -> None:
    # The window is a departure detector, not a lease: widening it must not make the hold
    # effectively permanent. A name left on screen is a privacy cost, not just a stale one.
    clock = {"t": 1000.0}
    config = SessionConfig.from_env()
    identity = RecognisedIdentity(
        idle_ttl_s=config.recognised_identity_idle_ttl_s,
        interaction_ttl_s=config.recognised_identity_interaction_ttl_s,
        monotonic_fn=lambda: clock["t"],
    )
    identity.remember(
        person_id="prs_tiger",
        display_name="Tiger",
        consent_version="v3",
        profile_id="warm_amber",
    )

    clock["t"] += config.recognised_identity_idle_ttl_s + 1
    assert identity.current() is None
    # And not absurdly long: a minute of a stranger seeing the last visitor's name is the
    # ceiling this is willing to pay for covering the cooldown.
    assert config.recognised_identity_idle_ttl_s <= 60.0
