"""The .ics feeds are prefetched on a timer; a presence GET never waits on them.

`get_label` used to fetch inline whenever the 15-minute cache had lapsed — up to
10 s of network inside an admin/bundle GET (and, on the event loop, inside every
other request too). tests/test_calendar_ics.py keeps covering the lazy path the
provider still uses when no refresher runs; this covers the background one.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import httpx
from control_plane_api.calendar_ics import IcsCalendarProvider
from doorboard_contracts import PresenceLabel

URL = "https://calendar.example/secret/basic.ics"
NOW = datetime(2026, 9, 7, 14, 0, tzinfo=UTC)

WEEKLY_CLASS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:cs101@example
DTSTART:20260907T133000Z
DTEND:20260907T145000Z
RRULE:FREQ=WEEKLY;BYDAY=MO
END:VEVENT
END:VCALENDAR
"""


class _CountingTransport:
    def __init__(self, body: bytes, *, delay_s: float = 0.0, status: int = 200) -> None:
        self.body = body
        self.delay_s = delay_s
        self.status = status
        self.calls = 0
        self.threads: list[str] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.threads.append(threading.current_thread().name)
        time.sleep(self.delay_s)
        return httpx.Response(self.status, content=self.body)


def _wait_for(predicate, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert predicate()


def test_get_label_reads_the_cache_and_never_fetches_once_the_refresher_runs() -> None:
    transport = _CountingTransport(WEEKLY_CLASS.encode(), delay_s=0.3)
    provider = IcsCalendarProvider({"owner": URL}, client=transport.client, refresh_s=3600)
    provider.start_background_refresh()
    try:
        _wait_for(lambda: provider.healthy)  # the timer's own first fetch has landed
        assert transport.calls == 1
        t0 = time.monotonic()
        entry = provider.get_label("owner", now=NOW)
        assert (time.monotonic() - t0) < 0.1  # cache read, not a 0.3 s fetch
        assert entry is not None and entry.label == PresenceLabel.AT_CLASS

        # Even a "stale" now does not trigger a fetch from the request path.
        stale_now = NOW.replace(year=2027)
        provider.get_label("owner", now=stale_now)
        assert transport.calls == 1
        assert all(name == "calendar-ics-refresh" for name in transport.threads)
    finally:
        provider.stop()


def test_before_the_first_refresh_lands_the_source_is_silent_not_blocking() -> None:
    transport = _CountingTransport(WEEKLY_CLASS.encode(), delay_s=1.0)
    provider = IcsCalendarProvider({"owner": URL}, client=transport.client, refresh_s=3600)
    provider.start_background_refresh()
    try:
        t0 = time.monotonic()
        assert provider.get_label("owner", now=NOW) is None  # nothing cached yet
        assert (time.monotonic() - t0) < 0.1
        _wait_for(lambda: provider.get_label("owner", now=NOW) is not None)
    finally:
        provider.stop()


def test_healthy_reflects_the_last_fetch_of_every_feed() -> None:
    ok = IcsCalendarProvider(
        {"owner": URL}, client=_CountingTransport(WEEKLY_CLASS.encode()).client
    )
    assert ok.healthy is False  # nothing fetched yet
    ok.refresh_all(now=NOW)
    assert ok.healthy is True

    broken = IcsCalendarProvider({"owner": URL}, client=_CountingTransport(b"", status=500).client)
    broken.refresh_all(now=NOW)
    assert broken.healthy is False

    assert IcsCalendarProvider({}).healthy is True  # no feeds configured: nothing to be unhealthy
