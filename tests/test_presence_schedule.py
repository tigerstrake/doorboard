"""ADR-0037: a nightly Recovery window that yields to any active Focus.

The behaviour asked for was specific: Recovery from 23:00 to 07:00, *except* when
another Focus is on, and back to Recovery the moment that Focus is turned off. The
last clause is the interesting one — it is why the schedule is computed live
rather than stored, and why a Focus-off has to clear its source rather than write
"available".

Pure unit tests of `resolve_presence` and the provider; no database, so they live
here rather than under apps/control-plane-api/tests/ where an autouse fixture
opens Postgres.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from control_plane_api.presence import (
    SOURCE_PRECEDENCE,
    NightlyScheduleProvider,
    SourceEntry,
    parse_window,
    resolve_presence,
)
from doorboard_contracts import PresenceLabel

WINDOW = parse_window("23:00-07:00")


def _provider(**kw) -> NightlyScheduleProvider:
    return NightlyScheduleProvider(WINDOW, label=PresenceLabel.SLEEPING, **kw)


def _local(hour: int, minute: int = 0, day: int = 22) -> datetime:
    """A local wall-clock instant, which is what the window is expressed in."""
    return datetime(2026, 8, day, hour, minute).astimezone()


# --- the window itself ------------------------------------------------------


@pytest.mark.parametrize("hour,minute", [(23, 0), (23, 30), (0, 15), (3, 0), (6, 59)])
def test_inside_the_window_reports_recovery(hour, minute):
    entry = _provider().get_label("owner", now=_local(hour, minute))
    assert entry is not None
    assert entry.label == PresenceLabel.SLEEPING


@pytest.mark.parametrize("hour,minute", [(7, 0), (7, 30), (12, 0), (22, 30), (22, 59)])
def test_outside_the_window_is_silent(hour, minute):
    assert _provider().get_label("owner", now=_local(hour, minute)) is None


def test_until_is_the_end_of_the_window_so_it_expires_itself():
    """The engine drops an expired entry, so no background task is needed."""
    entry = _provider().get_label("owner", now=_local(23, 30))
    assert entry is not None
    assert entry.until is not None
    local_end = entry.until.astimezone()
    assert (local_end.hour, local_end.minute) == (7, 0)
    # 23:30 -> 07:00 is tomorrow, not seven and a half hours ago.
    assert entry.until > _local(23, 30)


def test_after_midnight_until_is_this_mornings_seven():
    entry = _provider().get_label("owner", now=_local(2, 0))
    assert entry is not None
    assert entry.until is not None
    assert (entry.until - _local(2, 0)) < timedelta(hours=6)


def test_a_window_can_be_limited_to_one_subject():
    """One person's habit shouldn't be imposed on whoever shares the door."""
    provider = _provider(subject_ids=["owner"])
    assert provider.get_label("owner", now=_local(2, 0)) is not None
    assert provider.get_label("roommate", now=_local(2, 0)) is None


def test_no_window_configured_means_no_source():
    assert parse_window("") is None
    assert parse_window("   ") is None


@pytest.mark.parametrize("bad", ["23:00", "-07:00", "23:00-", "nope-07:00", "25:00-07:00"])
def test_a_malformed_window_raises_rather_than_disabling_silently(bad):
    with pytest.raises(ValueError):
        parse_window(bad)


# --- precedence: the actual requested behaviour -----------------------------


def test_schedule_sits_below_every_source_that_knows_something():
    """It is a standing assumption about the clock, so it must lose to real signals."""
    order = list(SOURCE_PRECEDENCE)
    assert order.index("schedule") > order.index("focus_shortcut")
    assert order.index("schedule") > order.index("geofence_label")
    assert order.index("schedule") > order.index("calendar")
    assert order.index("schedule") < order.index("default")


def test_recovery_wins_when_nothing_else_is_set():
    now = _local(2, 0)
    resolved = resolve_presence({"schedule": _provider().get_label("owner", now=now)}, now=now)
    assert resolved.label == PresenceLabel.SLEEPING
    assert resolved.source == "schedule"


def test_an_active_focus_overrides_recovery():
    """ "Except for when I have a different focus on"."""
    now = _local(2, 0)
    resolved = resolve_presence(
        {
            "focus_shortcut": SourceEntry(label=PresenceLabel.BUSY),
            "schedule": _provider().get_label("owner", now=now),
        },
        now=now,
    )
    assert resolved.label == PresenceLabel.BUSY
    assert resolved.source == "focus_shortcut"


def test_clearing_the_focus_brings_recovery_straight_back():
    """The clause that shaped the design.

    Nothing re-triggers the schedule: it is recomputed on every resolution, so a
    cleared focus_shortcut (entry is None) falls straight through to it.
    """
    now = _local(2, 0)
    entries = {
        "focus_shortcut": SourceEntry(label=PresenceLabel.BUSY),
        "schedule": _provider().get_label("owner", now=now),
    }
    assert resolve_presence(entries, now=now).source == "focus_shortcut"

    entries["focus_shortcut"] = None  # the Focus-off shortcut CLEARS the source
    assert resolve_presence(entries, now=now).source == "schedule"
    assert resolve_presence(entries, now=now).label == PresenceLabel.SLEEPING


def test_a_focus_off_that_writes_available_would_defeat_the_whole_thing():
    """Why the webhook had to learn to clear rather than only set.

    This is the failure mode if a Focus-off writes "available" instead of null:
    a real value in a higher source outranks the schedule, and the door sits on
    Available all night. Pinned so nobody 'simplifies' the clear away.
    """
    now = _local(2, 0)
    resolved = resolve_presence(
        {
            "focus_shortcut": SourceEntry(label=PresenceLabel.AVAILABLE),
            "schedule": _provider().get_label("owner", now=now),
        },
        now=now,
    )
    assert resolved.label == PresenceLabel.AVAILABLE
    assert resolved.source == "focus_shortcut"


def test_a_real_sleep_focus_still_wins_during_the_window():
    """The owner keeps iOS Sleep Focus for actually sleeping.

    It sets focus_shortcut, so it outranks the schedule — same label here, but the
    reported source differs, which is what makes the two distinguishable.
    """
    now = _local(2, 0)
    resolved = resolve_presence(
        {
            "focus_shortcut": SourceEntry(label=PresenceLabel.SLEEPING),
            "schedule": _provider().get_label("owner", now=now),
        },
        now=now,
    )
    assert resolved.source == "focus_shortcut"


def test_away_beats_recovery_because_location_knows_more():
    """At 2am and demonstrably not home, "Away" is the more useful truth."""
    now = _local(2, 0)
    resolved = resolve_presence(
        {
            "geofence_label": SourceEntry(label=PresenceLabel.AWAY),
            "schedule": _provider().get_label("owner", now=now),
        },
        now=now,
    )
    assert resolved.label == PresenceLabel.AWAY


def test_outside_the_window_it_falls_through_to_default():
    now = _local(12, 0)
    resolved = resolve_presence({"schedule": _provider().get_label("owner", now=now)}, now=now)
    assert resolved.source == "default"


def test_an_expired_focus_falls_through_to_recovery():
    """ "busy until 15:00" set yesterday must not outlive itself into the night."""
    now = _local(2, 0)
    resolved = resolve_presence(
        {
            "focus_shortcut": SourceEntry(label=PresenceLabel.BUSY, until=now - timedelta(hours=3)),
            "schedule": _provider().get_label("owner", now=now),
        },
        now=now,
    )
    assert resolved.source == "schedule"
    assert resolved.label == PresenceLabel.SLEEPING


# --- the cross-check that was missing --------------------------------------


def test_every_precedence_source_is_accepted_by_the_contract():
    """SOURCE_PRECEDENCE and the event contract's Literal must agree.

    This is the test that should have existed before `schedule` was added. Adding a
    source to the engine without adding it to StatusPresenceChangedPayload does not
    fail at startup, and does not fail while a higher source keeps winning — it
    fails the first time the new source WINS, as a 500 out of presence resolution,
    because sync_presence builds the outbound event from the resolved source.

    Shipped exactly that way on 2026-08-22: 28 tests passed because every one of
    them called resolve_presence directly and none exercised the publish path.
    """
    import typing

    from doorboard_contracts.events import StatusPresenceChangedPayload

    allowed = set(typing.get_args(StatusPresenceChangedPayload.model_fields["source"].annotation))
    missing = set(SOURCE_PRECEDENCE) - allowed
    assert not missing, (
        "these presence sources can win resolution but cannot be published, so the "
        f"first time one does it is a 500: {sorted(missing)}"
    )


def test_the_contract_declares_no_source_the_engine_cannot_produce():
    """The other direction: a Literal member nothing resolves to is dead weight."""
    import typing

    from doorboard_contracts.events import StatusPresenceChangedPayload

    allowed = set(typing.get_args(StatusPresenceChangedPayload.model_fields["source"].annotation))
    assert not (allowed - set(SOURCE_PRECEDENCE))


def test_a_resolved_schedule_can_actually_be_published():
    """End to end through the payload model, not just the resolver."""
    from doorboard_contracts.events import StatusPresenceChangedPayload

    now = _local(2, 0)
    resolved = resolve_presence({"schedule": _provider().get_label("owner", now=now)}, now=now)
    assert resolved.source == "schedule"
    payload = StatusPresenceChangedPayload(
        subject_id="owner",
        label=resolved.label,
        source=resolved.source,
        until=resolved.until,
    )
    assert payload.source == "schedule"
    assert payload.label == PresenceLabel.SLEEPING
    assert payload.until == resolved.until
