# ADR-0026: a dining recommendation names its hall as a field

**Status:** Accepted · **Date:** 2026-08-17

Extends `ambient.food_recommendation`. Additive; no existing field changes meaning. Same
shape of change as [ADR-0025](0025-satellite-pass-geometry.md) and made for the same reason.

## Context

`ambient.food_recommendation` carried `title`, `detail`, `provider`. The Stanford provider
builds that title as `f"{best_hall} — {internal.meal}"` and lists the backup inside
`detail` as `"backup: {backup_hall}"`. So the payload named a venue and a fallback venue —
in prose, formatted for a human, with the structure discarded on the way out.

The owner asked for the recommended hall to appear as a point on a campus map. Placing a
point requires knowing *which hall*, and the only way to get that from the old payload is to
split a display string on an em dash and regex a substring out of a sentence. That is a
consumer parsing a producer's presentation choices: the em dash, the word "backup", the
order of the clauses in `detail` all become load-bearing, and none of them are contractual.
Change the title format to read better and a map somewhere else silently stops working.

`packages/contracts` exists precisely so this does not happen.

## Decision

**The payload gains `hall` and `backup_hall`.** `str | None`, defaulting to `None`. The
integration's own `Recommendation` model gains the same two fields, and the Stanford provider
populates them from the `DiningRecommendation` it already has in hand — it was computing both
and then flattening them into English.

`title` and `detail` keep their exact current wording. They are what a text-only surface
renders, and they remain the primary display; the new fields are for consumers that need to
*do* something with the venue rather than show it.

**Optional, because not every provider has a named venue.** A manual entry ("Noodle soup") or
a future provider recommending a dish rather than a hall omits `hall`. A consumer must treat
absence as normal.

**The consumer resolves a hall to a location through a catalogue, and null is a valid
result.** `apps/door-ui/src/wallboard/campusMap.ts` holds real coordinates, geocoded from
OpenStreetMap, keyed by lowercase substring because the hall names originate in Stanford's
own menu site and are not ours to pin down. A name that matches nothing gets **no point** and
the panel says the hall is not in the catalogue. Nearest-match would put a confident dot on
the wrong building, which is worse than an empty map — the map is only worth having if a dot
on it is true.

**The prose title stays a documented fallback.** `hallFromTitle` recovers the hall from
`"{hall} — {meal}"` for events produced before this change, including anything in the
yesterday-cache the food job falls back to. It is a compatibility shim for old data, not a
supported path for new producers.

## Consequences

- The cache holds `Recommendation` objects; the new fields are optional, so entries written
  before this change still load. They come back with `hall = None` and the title fallback
  covers them.
- The coordinate catalogue is a hardcoded list that will drift as campus changes. It is
  tested for what can be checked mechanically — every hall inside the framed view, no
  duplicate aliases, and relative geography (Wilbur east of Stern, Ricker west of the lake) —
  so a transposed coordinate fails a test rather than shipping.
- Three halls are located at their residence building rather than their dining room, and the
  panel says so on screen instead of implying a precision it does not have.
- The map draws every hall, not just the pick. One dot on an empty field has no scale;
  "the near cluster" versus "past the lake" is the actual information.
