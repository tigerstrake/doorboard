# ADR-0032: a keyless aircraft feed, because anonymous OpenSky cannot answer

**Status:** Accepted · **Date:** 2026-08-18

Adds a second provider behind the existing `AircraftProvider` interface. No contract change, no
new package dependency (`httpx`, already used).

## Context

The flights channel has never shown live aircraft on this door. Three separate causes were
fixed first — the poll interval was not passed through the compose stack, `OPENSKY_CLIENT_ID`
was mapped onto the pre-OAuth2 `OPENSKY_USERNAME`, and a failed fetch was published as an empty
sky — and it still showed nothing, because the remaining cause is not tunable:

```
GET https://opensky-network.org/api/states/all?lamin=37.40&...  ->  429
x-rate-limit-retry-after-seconds: 16841
Too many requests
```

Nearly five hours of refusal. OpenSky's anonymous tier allows a few hundred requests per IP per
day. Even a five-minute cadence — already too stale for "what is overhead", since an airliner
covers ~10 km a minute — cannot be sustained reliably, and the owner cannot configure their way
out of it. The documented answer, "register an API client", is correct and is a task only the
owner can do; meanwhile the tile says nothing about a sky containing hundreds of aircraft.

At the same moment OpenSky was refusing, `opendata.adsb.fi` returned 26 aircraft within 50 nm of
the door, with no key.

## Decision

**Add `AdsbFiAircraftProvider`, and choose the feed by capability rather than preference.**
`AIRCRAFT_PROVIDER=auto` (the default) resolves to OpenSky when `OPENSKY_CLIENT_ID` is set and
adsb.fi otherwise. `opensky` and `adsbfi` force the choice.

This is deliberately not "adsb.fi is better". OpenSky is global and community-run rather than
dependent on one aggregator's goodwill, so it stays preferred the moment credentials exist —
and the switch back is automatic, with no second configuration step to forget.

**adsb.fi returns more than OpenSky does.** Registration and aircraft type arrive inline, where
the enrichment step otherwise fetches them from adsbdb and planespotters. The enricher is
unchanged and still fills any gaps.

**Aircraft on the ground are dropped.** adsb.fi reports them, and they are the *nearest* returns
— a tile called "overhead aircraft" that leads with jets parked at Palo Alto airport is
answering a different question than the one asked. Anything without a position is dropped too:
unplaceable is unusable.

**The honesty rule carries over.** A failed fetch with no cache raises `AircraftDataUnavailable`
rather than returning an empty list, and `as_of` records the observation rather than the send —
both the same distinctions the OpenSky path had to learn.

## Consequences

- A second external dependency for a nice-to-have channel. Bounded: it is one HTTP GET, behind
  the same cooldown, and its failure mode is a tile that says it has nothing rather than a door
  that misbehaves. Nothing on the critical path touches it.
- adsb.fi's coverage is contributor-dependent and not identical to OpenSky's. For a doorboard
  answering "what is flying over the house", coverage over one metro area is what matters, and
  it demonstrably has it here.
- `AIRCRAFT_RADIUS_NM` replaces the bounding box for this provider — it takes a point and a
  radius. The OpenSky path keeps its bbox settings unchanged.
- Registering an OpenSky client is still worth doing, and now an upgrade rather than a
  prerequisite.
