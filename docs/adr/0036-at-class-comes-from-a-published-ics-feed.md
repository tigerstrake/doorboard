# ADR-0036: `at_class` comes from a published .ics feed

**Status:** Accepted · **Date:** 2026-08-22

## Context

`at_class` existed as a label with no way to set it. The presence precedence chain already had a
`calendar` slot — `manual > focus_shortcut > geofence_label > calendar > default` — and
`presence.py` defined a `CalendarProvider` Protocol, but it was wired to `MockCalendarProvider`,
which always answers "no signal". The original brief said "real calendar wiring is a later
brief". This is that.

The owner has a calendar containing only classes, so every event on it means "in class". No
keyword matching or heuristics are needed, and none are used.

## Decision

`IcsCalendarProvider` fetches a published `.ics` URL per subject and returns `at_class` with
`until` set to the current event's end.

Two properties of the existing design made this shape the right one, rather than pushing updates
in from Home Assistant:

- **`calendar` is not a stored source.** It is queried fresh on every resolution, so it cannot
  get stuck reading "At Class" at midnight. A push/webhook design is edge-triggered: one missed
  "event ended" and the door lies until something else writes.
- **`SourceEntry` carries `until`**, and the engine expires time-bounded labels with no
  background scheduler. A class ending at 15:20 reverts on its own.

It also keeps precedence honest. The rejected alternative — an HA calendar integration pushing
through the existing webhooks — would have had to write into the `focus_shortcut` or
`geofence_label` slot, because `calendar` isn't writable. A scheduled class and a deliberate Focus
change would then overwrite each other, when the whole point of the ordering is that a manual
choice wins.

Config is `PRESENCE_CALENDAR_ICS_URLS`, as `owner=https://…,roommate=https://…`. Per-subject
because two people share this door and their schedules differ. Empty — the default — means no
calendar source at all, and presence resolves exactly as it did before.

**ICS rather than a calendar API.** A `.ics` URL works with Google's "secret address in iCal
format", an iCloud public link, a Canvas calendar feed, or a university course export, with no
OAuth flow, no client registration, and no token refresh on the NUC. Recurrence is handled by
`recurring-ical-events` rather than by hand: a class schedule is `RRULE`-based, with `EXDATE` for
cancelled weeks and `RECURRENCE-ID` for moved ones, and hand-rolling that would get cancelled
classes wrong in the direction that matters — claiming someone is in a class that isn't running.

### Privacy

**Only a label and an end time leave the provider.** Summaries, locations, descriptions and
attendees are read to find the current event and then discarded. They are never returned, logged,
or persisted. A presence system that quietly accumulated someone's course list with room numbers
would be a materially different and worse thing than a door that says "At Class", and the
distinction is asserted by a test rather than promised by a comment.

The feed URLs are **never logged, including on failure**. A "secret address in iCal format" is a
bearer credential in a URL: anyone holding it can read the whole calendar. The startup log records
which *subjects* have a calendar configured, never the addresses.

All-day events are ignored. On a course calendar those are overwhelmingly "Spring Break" or "No
class" markers, and treating one as a class would pin the door to "At Class" for a week.

### Failure behaviour

Every failure path returns `None`, which degrades to the next source down. A fetch error, a
timeout, an unparseable feed, an oversized response, or a recurrence-expansion error must not be
able to break presence resolution — the calendar is an input to a display, not a dependency of
one. Failures are cached for the refresh interval too, so a broken feed is retried on a schedule
rather than on every incoming request.

## Consequences

- One new dependency on control-plane-api (`recurring-ical-events`, which brings `icalendar` and
  `python-dateutil`). The NUC already makes outbound calls for ambient data, so this adds no new
  trust boundary; the door still makes none.
- No contract change, so this deploys to the NUC alone.
- Tests live in the repo-level `tests/` rather than `apps/control-plane-api/tests/`, because that
  package's conftest opens a Postgres connection for every test via an autouse fixture and these
  need no database.
- A wrong or expired feed URL is silent by design. `calendar_source_enabled` at startup and
  `calendar_ics_fetch_failed` afterwards are the only signals, which is the accepted cost of not
  logging credentials.
