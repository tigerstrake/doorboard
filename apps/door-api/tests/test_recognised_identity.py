"""The interaction-scoped identity (ADR-0020).

The bug these pin: the door greeted a recognised person, then ten seconds later the
approach timer returned the session to IDLE and cleared the name — so tapping through to
Check In found nothing and offered "Guest" only. Recognition had worked; the door had
simply stopped remembering while the person was still standing there.
"""

from __future__ import annotations

import pytest
from door_api.recognised_identity import RecognisedIdentity


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _holder(clock: _Clock, *, idle: float = 12.0, interaction: float = 120.0):
    return RecognisedIdentity(idle_ttl_s=idle, interaction_ttl_s=interaction, monotonic_fn=clock)


def _remember(holder: RecognisedIdentity, *, person_id: str = "prs_1", consent: str = "v3") -> None:
    holder.remember(
        person_id=person_id,
        display_name="Tiger" if person_id == "prs_1" else "Sam",
        consent_version=consent,
        profile_id="warm_amber",
    )


def test_a_passerby_is_forgotten_on_the_idle_window() -> None:
    """Recognised and then nothing: the name must not linger in memory."""
    clock = _Clock()
    holder = _holder(clock)
    _remember(holder)

    clock.advance(11.0)
    assert holder.current() is not None
    clock.advance(2.0)
    assert holder.current() is None


def test_using_the_doorpad_holds_the_identity_for_the_interaction() -> None:
    """The regression that mattered: reading the screen must not lose your name."""
    clock = _Clock()
    holder = _holder(clock)
    _remember(holder)

    holder.touch()  # visitor taps "Visitor Check-In"
    clock.advance(30.0)  # ...and takes half a minute to read it

    held = holder.current()
    assert held is not None
    assert held.display_name == "Tiger"


def test_the_interaction_window_still_ends() -> None:
    clock = _Clock()
    holder = _holder(clock)
    _remember(holder)
    holder.touch()

    clock.advance(121.0)
    assert holder.current() is None


def test_a_touch_cannot_resurrect_an_expired_identity() -> None:
    """Otherwise the first tap after a gap re-attributes the door to whoever was last seen."""
    clock = _Clock()
    holder = _holder(clock)
    _remember(holder)
    clock.advance(60.0)
    assert holder.current() is None

    holder.touch()

    assert holder.current() is None


def test_a_different_person_replaces_rather_than_extends() -> None:
    """Two people at the door: the newest match wins, and windows do not merge."""
    clock = _Clock()
    holder = _holder(clock)
    _remember(holder, person_id="prs_1")
    holder.touch()  # prs_1 is mid-interaction on the long window

    _remember(holder, person_id="prs_2")

    held = holder.current()
    assert held is not None
    assert held.person_id == "prs_2"
    # prs_2 arrived but has touched nothing, so they are on the short window and do not
    # inherit prs_1's remaining two minutes.
    clock.advance(13.0)
    assert holder.current() is None


def test_re_recognising_the_same_person_keeps_their_interaction_window() -> None:
    clock = _Clock()
    holder = _holder(clock)
    _remember(holder)
    holder.touch()
    clock.advance(30.0)

    _remember(holder)  # a fresh identity_stable mid-interaction

    clock.advance(60.0)  # still inside the touched window
    assert holder.current() is not None


def test_forget_person_only_drops_the_named_person() -> None:
    """Unenrollment must propagate without evicting whoever else is at the door."""
    clock = _Clock()
    holder = _holder(clock)
    _remember(holder, person_id="prs_1")

    assert holder.forget_person("prs_someone_else") is False
    assert holder.current() is not None
    assert holder.forget_person("prs_1") is True
    assert holder.current() is None


def test_forget_is_immediate() -> None:
    """Privacy mode and session end take the name with them, not on a timer."""
    clock = _Clock()
    holder = _holder(clock)
    _remember(holder)
    holder.touch()

    holder.forget()

    assert holder.current() is None
    assert holder.seconds_remaining() == 0.0


@pytest.mark.parametrize("consent", ["v1", "v2", None, "nonsense"])
def test_the_holder_does_not_itself_gate_consent(consent: str | None) -> None:
    """It records what it was told; the attribution gate lives in one place in app.py.

    Pinned because a second consent check here would be a second place to get it wrong,
    and the two could disagree about whether a name may be attached to a write.
    """
    clock = _Clock()
    holder = _holder(clock)
    _remember(holder, consent=consent)  # type: ignore[arg-type]

    held = holder.current()
    assert held is not None
    assert held.consent_version == consent
