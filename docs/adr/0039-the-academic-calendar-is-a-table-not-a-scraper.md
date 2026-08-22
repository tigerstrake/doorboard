# ADR-0039: the academic calendar is a table, not a scraper

**Status:** Accepted · **Date:** 2026-08-22

## Context

The owner wanted a countdown tile — days until finals, until break, until move-out — filtered to
undergraduate dates and "only the important stuff". Dorm-relevant in a way weather is not.

Every other ambient tile fetches from somewhere: aircraft from adsb.fi, satellites from a pass
provider, birds from a BirdNET Pi. The obvious move was to do the same here.

The university publishes its academic calendar as **a web page with no iCalendar feed**. The
official page also refuses automated fetches (HTTP 403). So "fetch it" means scraping HTML.

## Decision

**A JSON date table in the repo, read from disk.** No outbound request at all.

`integrations/academic-calendar/stanford-2026-2027.json` holds eleven dates — quarter starts,
last days of classes, finals windows, Thanksgiving, spring break, Commencement. Deliberately not
grade deadlines, degree conferral, or Final Recommending Lists: those are administrative, and the
owner asked for what a dorm resident plans around.

A scraper for something that changes **three times a year** would be the most fragile component in
the system, and it would fail in the worst way — silently, into a wrong number on a wall. Hand-
curating eleven dates once a year is less work than maintaining a parser against a page nobody
warns you they redesigned.

The table also means this job is **exempt from the external-poll budget** (ADR-0038) by
construction rather than by argument. There is no remote service to rate-limit, no free tier to
exhaust, and no credential to leak. It appears in that budget table as `demand_gated` with a
target of "a local JSON date table — no network at all", which is the honest entry.

### Publishing nothing beats publishing something wrong

`load_academic_milestones` returns `None` — and the job then publishes no event — when the table
is missing, unreadable, or **has run out of future dates**. That last case is the one that
matters: when the academic year ends and nobody refreshes the table, the tile shows its own empty
state instead of counting down to a date that has already passed. A stale countdown looks
authoritative and is wrong, which is worse than a blank tile.

One malformed row is skipped rather than discarding the year, so a single typo does not cost the
other ten dates.

**Days are counted in local time**, not UTC. "Days until finals" is counted in the calendar the
person is living in, and on a UTC-configured container those differ for several hours a day —
exactly the off-by-one nobody notices until the morning it is wrong. (The same class of bug as
ADR-0037's timezone miss, found the same day.)

### Contract

`ambient.academic_countdown` carries the leading milestone plus the next few, so the tile can say
"then X in N days" without a second event. `source` travels with it so a stale table is
identifiable on screen rather than only in a log.

Per ADR-0031, a new event *type* is additive and safe in a way a new field is not — an older
consumer ignores an unknown type at the subscription level rather than rejecting it. The NUC still
goes first, since it validates at ingest.

`FEATURE_ACADEMIC_COUNTDOWN` defaults false: the tile appears when the owner turns it on.

## Consequences

- The table needs replacing once a year. That is the whole maintenance cost, and it is visible in
  git rather than hidden in a parser.
- A tile with no focus channel, because a countdown is one number and there is nothing to expand
  into.
- If Stanford ever publishes an ICS feed, `IcsCalendarProvider` from ADR-0036 already exists and
  this becomes a provider swap. Worth revisiting then; not worth guessing at now.
- Tests assert the shipped table's internal consistency — chronological order, finals after the
  last day of classes, no `kind` the contract rejects — because a transcription error in a
  hand-curated table is the likely failure, and it is cheap to catch.
