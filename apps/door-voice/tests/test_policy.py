"""ADR-0034 is a set of refusals; these are the refusals.

A screen a foot away shows a name to the person being greeted. A speaker tells a
shared corridor. So every one of these "no" answers is the feature working.
"""

from __future__ import annotations

from datetime import time as dtime

import pytest
from door_voice.policy import GreetingPolicy, in_quiet_hours, parse_quiet_hours

NOON = dtime(12, 0)


def make_policy(**overrides) -> GreetingPolicy:
    defaults = {
        "enabled": True,
        "allowed_person_ids": frozenset({"prs_tiger"}),
        "cooldown_s": 600.0,
        "quiet_hours": (dtime(22, 0), dtime(8, 0)),
    }
    defaults.update(overrides)
    return GreetingPolicy(**defaults)


def refuse(policy: GreetingPolicy, **kw) -> str | None:
    args = {
        "person_id": "prs_tiger",
        "display_name": "Tiger",
        "now_monotonic": 1000.0,
        "now_local_time": NOON,
    }
    args.update(kw)
    return policy.refusal_reason(**args)


def test_greets_an_opted_in_person_in_daytime():
    assert refuse(make_policy()) is None


def test_disabled_by_default_is_the_whole_point():
    assert refuse(make_policy(enabled=False)) == "feature_disabled"


def test_an_enrolled_person_who_never_opted_in_is_never_named_aloud():
    """Enrolment consent covers the doorpad screen, not the corridor."""
    policy = make_policy(allowed_person_ids=frozenset())
    assert refuse(policy) == "not_opted_in"


def test_unrecognised_visitor_is_never_greeted():
    assert refuse(make_policy(), person_id=None) == "no_person_id"


@pytest.mark.parametrize("name", [None, "", "   "])
def test_no_usable_name_means_no_greeting(name):
    assert refuse(make_policy(), display_name=name) == "no_display_name"


@pytest.mark.parametrize("hour", [22, 23, 0, 3, 7])
def test_quiet_hours_silence_even_an_opted_in_person(hour):
    assert refuse(make_policy(), now_local_time=dtime(hour, 30 if hour else 0)) == "quiet_hours"


@pytest.mark.parametrize("hour", [8, 12, 21])
def test_outside_quiet_hours_it_speaks(hour):
    assert refuse(make_policy(), now_local_time=dtime(hour, 15)) is None


def test_cooldown_blocks_a_repeat_and_then_releases():
    policy = make_policy(cooldown_s=600.0)
    policy.record_spoken("prs_tiger", 1000.0)
    assert refuse(policy, now_monotonic=1100.0) == "cooldown"
    assert refuse(policy, now_monotonic=1599.0) == "cooldown"
    assert refuse(policy, now_monotonic=1600.0) is None


def test_cooldown_is_per_person():
    policy = make_policy(allowed_person_ids=frozenset({"prs_a", "prs_b"}))
    policy.record_spoken("prs_a", 1000.0)
    assert refuse(policy, person_id="prs_a", now_monotonic=1100.0) == "cooldown"
    assert refuse(policy, person_id="prs_b", now_monotonic=1100.0) is None


def test_unenrolment_clears_the_cooldown():
    """ADR-0009: a purged person leaves no state behind, including this."""
    policy = make_policy()
    policy.record_spoken("prs_tiger", 1000.0)
    assert refuse(policy, now_monotonic=1100.0) == "cooldown"
    policy.forget_person("prs_tiger")
    assert refuse(policy, now_monotonic=1100.0) is None


def test_refusals_are_checked_in_privacy_order():
    """Disabled beats everything; opt-in is checked before the clock.

    If quiet hours were tested first, a non-opted-in person would be reported as
    "quiet_hours" — which reads like "it would have said your name at 9am".
    """
    policy = make_policy(enabled=False, allowed_person_ids=frozenset())
    assert refuse(policy, now_local_time=dtime(23, 0)) == "feature_disabled"
    policy = make_policy(allowed_person_ids=frozenset())
    assert refuse(policy, now_local_time=dtime(23, 0)) == "not_opted_in"


# --- quiet-hours window parsing ------------------------------------------


def test_window_wrapping_midnight():
    window = parse_quiet_hours("22:00-08:00")
    assert in_quiet_hours(dtime(23, 59), window)
    assert in_quiet_hours(dtime(0, 0), window)
    assert in_quiet_hours(dtime(7, 59), window)
    assert not in_quiet_hours(dtime(8, 0), window)
    assert not in_quiet_hours(dtime(21, 59), window)


def test_window_within_one_day():
    window = parse_quiet_hours("13:00-14:00")
    assert in_quiet_hours(dtime(13, 30), window)
    assert not in_quiet_hours(dtime(12, 59), window)
    assert not in_quiet_hours(dtime(14, 0), window)


def test_empty_window_disables_quiet_hours():
    assert parse_quiet_hours("") is None
    assert parse_quiet_hours("   ") is None
    assert not in_quiet_hours(dtime(3, 0), None)


def test_zero_width_window_reads_as_always_quiet():
    """start == end is ambiguous; the safe reading is silence, not all-night."""
    assert in_quiet_hours(dtime(3, 0), parse_quiet_hours("09:00-09:00"))


@pytest.mark.parametrize("bad", ["22:00", "not-a-time-08:00", "22:00-", "-08:00", "25:00-08:00"])
def test_a_malformed_window_raises_rather_than_silently_announcing_all_night(bad):
    with pytest.raises(ValueError):
        parse_quiet_hours(bad)
