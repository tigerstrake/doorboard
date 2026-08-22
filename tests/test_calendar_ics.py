"""ADR-0036: `at_class` from a published .ics feed.

No database and no network: the provider takes an injected httpx client, so these
are pure unit tests of the parts that actually go wrong — recurrence, cancelled
classes, timezones, all-day events, and what does *not* come back out.

Lives here rather than under apps/control-plane-api/tests/ deliberately: that
package's conftest has an autouse fixture that opens a Postgres connection, so
every test in it needs a database. These need none, and shouldn't be skipped on a
machine without one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from control_plane_api.calendar_ics import IcsCalendarProvider, parse_subject_urls
from doorboard_contracts import PresenceLabel

URL = "https://calendar.example/secret/basic.ics"


def _client(body: str | bytes, status: int = 200) -> httpx.Client:
    payload = body.encode() if isinstance(body, str) else body

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _provider(body: str | bytes, status: int = 200, **kw) -> IcsCalendarProvider:
    return IcsCalendarProvider({"owner": URL}, client=_client(body, status), **kw)


# A weekly Monday class, 13:30-14:50 UTC, with one cancelled week (EXDATE).
WEEKLY_CLASS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:cs101@example
DTSTART:20260907T133000Z
DTEND:20260907T145000Z
RRULE:FREQ=WEEKLY;BYDAY=MO
EXDATE:20260914T133000Z
SUMMARY:CS 101 Lecture
LOCATION:Gates B01
DESCRIPTION:Bring the problem set
END:VEVENT
END:VCALENDAR
"""


def test_reports_at_class_during_a_lecture():
    provider = _provider(WEEKLY_CLASS)
    entry = provider.get_label("owner", now=datetime(2026, 9, 7, 14, 0, tzinfo=UTC))
    assert entry is not None
    assert entry.label == PresenceLabel.AT_CLASS
    # `until` is the class end, so the engine expires it without a scheduler.
    assert entry.until == datetime(2026, 9, 7, 14, 50, tzinfo=UTC)


def test_silent_outside_class_hours():
    provider = _provider(WEEKLY_CLASS)
    assert provider.get_label("owner", now=datetime(2026, 9, 7, 9, 0, tzinfo=UTC)) is None
    assert provider.get_label("owner", now=datetime(2026, 9, 7, 23, 0, tzinfo=UTC)) is None


def test_recurrence_carries_to_later_weeks():
    """A class schedule is RRULE-based; only expanding the first week is useless."""
    provider = _provider(WEEKLY_CLASS)
    entry = provider.get_label("owner", now=datetime(2026, 9, 21, 14, 0, tzinfo=UTC))
    assert entry is not None
    assert entry.until == datetime(2026, 9, 21, 14, 50, tzinfo=UTC)


def test_a_cancelled_class_is_not_a_class():
    """EXDATE means that week's lecture is off — the door must not claim otherwise."""
    provider = _provider(WEEKLY_CLASS)
    assert provider.get_label("owner", now=datetime(2026, 9, 14, 14, 0, tzinfo=UTC)) is None


def test_nothing_about_the_class_itself_is_returned():
    """The privacy guarantee, asserted rather than promised.

    The feed carries a course code, a room number and a description. A presence
    system that accumulated those would be a different and much worse thing than a
    door that says "At Class".
    """
    provider = _provider(WEEKLY_CLASS)
    entry = provider.get_label("owner", now=datetime(2026, 9, 7, 14, 0, tzinfo=UTC))
    assert entry is not None
    leaked = repr(entry) + str(entry.__dict__ if hasattr(entry, "__dict__") else entry)
    for secret in ("CS 101", "Gates", "problem set"):
        assert secret not in leaked
    # The dataclass carries exactly two things.
    assert entry.label is not None
    assert entry.until is not None


ALL_DAY = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:break@example
DTSTART;VALUE=DATE:20260907
DTEND;VALUE=DATE:20260914
SUMMARY:Spring Break
END:VEVENT
END:VCALENDAR
"""


def test_all_day_entries_are_ignored():
    """On a course calendar these are holidays and "no class" markers.

    Treating one as a class would pin the door to "At Class" for a whole week.
    """
    provider = _provider(ALL_DAY)
    assert provider.get_label("owner", now=datetime(2026, 9, 9, 14, 0, tzinfo=UTC)) is None


LOCAL_TZ_CLASS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VTIMEZONE
TZID:America/Los_Angeles
BEGIN:DAYLIGHT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
DTSTART:20260308T020000
TZNAME:PDT
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:tz@example
DTSTART;TZID=America/Los_Angeles:20260907T093000
DTEND;TZID=America/Los_Angeles:20260907T105000
SUMMARY:Morning seminar
END:VEVENT
END:VCALENDAR
"""


def test_zoned_events_resolve_to_the_right_utc_instant():
    """A 09:30 Pacific class is 16:30 UTC. Getting this wrong is a 7-hour error."""
    provider = _provider(LOCAL_TZ_CLASS)
    assert provider.get_label("owner", now=datetime(2026, 9, 7, 17, 0, tzinfo=UTC)) is not None
    # 09:00 Pacific — before it starts.
    assert provider.get_label("owner", now=datetime(2026, 9, 7, 16, 0, tzinfo=UTC)) is None


def test_overlapping_classes_use_the_later_end():
    """Back-to-back or double-booked, presence should clear when the last one ends."""
    body = WEEKLY_CLASS.replace(
        "END:VCALENDAR",
        """BEGIN:VEVENT
UID:lab@example
DTSTART:20260907T140000Z
DTEND:20260907T163000Z
SUMMARY:Lab
END:VEVENT
END:VCALENDAR""",
    )
    provider = _provider(body)
    entry = provider.get_label("owner", now=datetime(2026, 9, 7, 14, 30, tzinfo=UTC))
    assert entry is not None
    assert entry.until == datetime(2026, 9, 7, 16, 30, tzinfo=UTC)


# --- degradation: a calendar outage must never break presence ---------------


@pytest.mark.parametrize(
    "body,status",
    [("", 500), ("not an ical at all", 200), ("", 404)],
)
def test_a_broken_feed_degrades_to_no_signal(body, status):
    provider = _provider(body, status)
    assert provider.get_label("owner", now=datetime(2026, 9, 7, 14, 0, tzinfo=UTC)) is None


def test_an_unconfigured_subject_is_silent():
    """The roommate has no feed; that must not borrow the owner's."""
    provider = _provider(WEEKLY_CLASS)
    assert provider.get_label("roommate", now=datetime(2026, 9, 7, 14, 0, tzinfo=UTC)) is None


def test_the_feed_is_cached_between_resolutions():
    """Presence resolves on every request; fetching each time would hammer the feed."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=WEEKLY_CLASS.encode())

    provider = IcsCalendarProvider(
        {"owner": URL},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        refresh_s=900.0,
    )
    base = datetime(2026, 9, 7, 14, 0, tzinfo=UTC)
    for offset in (0, 1, 2, 60):
        provider.get_label("owner", now=base + timedelta(seconds=offset))
    assert calls == 1

    # ...and refetched once the interval lapses.
    provider.get_label("owner", now=base + timedelta(seconds=901))
    assert calls == 2


def test_a_failed_fetch_is_also_cached_so_it_is_not_retried_per_request():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    provider = IcsCalendarProvider(
        {"owner": URL},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        refresh_s=900.0,
    )
    base = datetime(2026, 9, 7, 14, 0, tzinfo=UTC)
    for offset in (0, 1, 2):
        assert provider.get_label("owner", now=base + timedelta(seconds=offset)) is None
    assert calls == 1


# --- config parsing ---------------------------------------------------------


def test_parses_per_subject_urls():
    assert parse_subject_urls("owner=https://a/x.ics, roommate=https://b/y.ics") == {
        "owner": "https://a/x.ics",
        "roommate": "https://b/y.ics",
    }


def test_one_malformed_pair_does_not_discard_the_rest():
    """A typo in the roommate's entry must not cost the owner their calendar."""
    assert parse_subject_urls("owner=https://a/x.ics,garbage,=nourl,sub=") == {
        "owner": "https://a/x.ics"
    }


def test_empty_config_means_no_subjects():
    assert parse_subject_urls("") == {}
    assert parse_subject_urls("   ,  ") == {}
