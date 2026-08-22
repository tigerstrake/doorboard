"""Derive `at_class` from a published .ics calendar (ADR-0036).

Fills the `CalendarProvider` hole that presence.py left as a stub. The calendar
source sits below `focus_shortcut` and `geofence_label` in precedence, so a manual
Focus change still overrides a scheduled class rather than fighting it.

Two properties of the surrounding design shape this:

  * `calendar` is not a stored source — it is queried fresh on every resolution,
    so it cannot get stuck reading "At Class" at midnight.
  * `SourceEntry` carries `until`, and the engine expires time-bounded labels with
    no background scheduler, so a class ending at 15:20 reverts on its own.

PRIVACY. Only a label and an end time ever leave this module. Event summaries,
locations, descriptions and attendees are read to find the current event and then
discarded — they are never returned, logged, or persisted. A presence system that
quietly accumulated someone's course list, with room numbers, would be a different
and much worse thing than a door that says "At Class". There is a test for this.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
import icalendar
import recurring_ical_events
from doorboard_contracts import PresenceLabel

from control_plane_api.presence import SourceEntry

logger = logging.getLogger("control_plane_api.calendar_ics")

# A calendar big enough to matter is still small; this is a guard against a
# misconfigured URL pointing at something enormous.
_MAX_ICS_BYTES = 8 * 1024 * 1024


def parse_subject_urls(raw: str) -> dict[str, str]:
    """Parse ``owner=https://...,roommate=https://...`` into a mapping.

    Per-subject rather than one global URL: two people share this door and their
    schedules are not the same. An entry with no ``=`` is skipped rather than
    raising, so one malformed pair cannot take presence resolution down with it.
    """
    urls: dict[str, str] = {}
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        subject, sep, url = item.partition("=")
        if not sep or not subject.strip() or not url.strip():
            logger.warning("calendar_ics_bad_mapping", extra={"entry_len": len(item)})
            continue
        urls[subject.strip()] = url.strip()
    return urls


@dataclass
class _Cached:
    calendar: icalendar.Calendar | None
    fetched_at: datetime


class IcsCalendarProvider:
    """Reads published .ics feeds and reports whether a subject is in class now.

    Implements the ``CalendarProvider`` protocol. Never raises: a fetch failure or
    an unparseable feed returns None, which degrades to the next source down the
    precedence chain. A calendar outage must not be able to break presence.
    """

    def __init__(
        self,
        subject_urls: Mapping[str, str],
        *,
        label: PresenceLabel = PresenceLabel.AT_CLASS,
        refresh_s: float = 900.0,
        timeout_s: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._urls = dict(subject_urls)
        self._label = label
        self._refresh_s = refresh_s
        self._timeout_s = timeout_s
        self._client = client
        self._cache: dict[str, _Cached] = {}

    # -- fetching ----------------------------------------------------------

    def _fetch(self, url: str) -> icalendar.Calendar | None:
        try:
            client = self._client or httpx.Client(timeout=self._timeout_s, follow_redirects=True)
            close_after = self._client is None
            try:
                response = client.get(url)
                response.raise_for_status()
                body = response.content
            finally:
                if close_after:
                    client.close()
        except Exception as exc:  # noqa: BLE001 - any failure degrades, never raises
            # Deliberately does not log the URL: these are secret-address feeds
            # ("Secret address in iCal format"), i.e. bearer credentials in a URL.
            logger.warning("calendar_ics_fetch_failed", extra={"error_class": type(exc).__name__})
            return None
        if len(body) > _MAX_ICS_BYTES:
            logger.warning("calendar_ics_too_large", extra={"bytes": len(body)})
            return None
        try:
            return icalendar.Calendar.from_ical(body)
        except Exception as exc:  # noqa: BLE001
            logger.warning("calendar_ics_unparseable", extra={"error_class": type(exc).__name__})
            return None

    def _calendar_for(self, subject_id: str, now: datetime) -> icalendar.Calendar | None:
        url = self._urls.get(subject_id)
        if not url:
            return None
        cached = self._cache.get(subject_id)
        if cached is not None and (now - cached.fetched_at) < timedelta(seconds=self._refresh_s):
            return cached.calendar
        calendar = self._fetch(url)
        # Cache failures too, so a broken feed is retried on the refresh interval
        # rather than on every single presence resolution.
        self._cache[subject_id] = _Cached(calendar=calendar, fetched_at=now)
        return calendar

    # -- the protocol ------------------------------------------------------

    def get_label(self, subject_id: str, *, now: datetime) -> SourceEntry | None:
        calendar = self._calendar_for(subject_id, now)
        if calendar is None:
            return None
        try:
            occurrences = recurring_ical_events.of(calendar).at(now)
        except Exception as exc:  # noqa: BLE001
            logger.warning("calendar_ics_expand_failed", extra={"error_class": type(exc).__name__})
            return None

        latest_end: datetime | None = None
        for event in occurrences:
            end = _aware_end(event)
            if end is None or end <= now:
                continue
            if latest_end is None or end > latest_end:
                latest_end = end

        if latest_end is None:
            return None
        # Only the label and the end time. Nothing about WHAT the class is.
        return SourceEntry(label=self._label, until=latest_end)


def _aware_end(event: object) -> datetime | None:
    """End of an occurrence as an aware UTC datetime, or None to skip it.

    All-day (date-valued) events are deliberately skipped. On a course calendar
    those are far more likely to be "Spring Break" or "No class" markers than an
    actual class, and treating one as a class would pin the door to "At Class" for
    a whole day.
    """
    try:
        raw = event.get("DTEND") or event.get("DTSTART")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return None
    if raw is None:
        return None
    value = getattr(raw, "dt", None)
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # A floating time means "local wherever this is read". The door is the
            # only reader, so the container's zone is the right interpretation.
            value = value.astimezone()
        return value.astimezone(UTC)
    if isinstance(value, date):
        # Date-only => all-day. Skipped, see docstring.
        return None
    return None
