# ADR-0041: the globe shows every orbit, not one pass

**Status:** Accepted · **Date:** 2026-08-23

Supersedes the "one selected pass" scope of the satellite channel set in
[handoff §13](../handoff/doorboard_architecture_handoff.md) ("display selected next pass, not
every pass") and extends [ADR-0025](0025-satellite-pass-geometry.md) and
[ADR-0030](0030-a-satellite-pass-carries-its-ground-track.md). The `ambient.satellite_pass`
event and its renderer are unchanged; this adds alongside them. Deployment ordering per
[ADR-0031](0031-additive-contract-fields-are-not-backward-compatible.md).

## Context

ADR-0030 put a globe on the wallboard and drew the single next *visible* pass on it: the one
satellite worth going outside for tonight, its rise-to-set arc, and where it comes nearest the
door. That is the right thing to show someone deciding whether to look up.

The owner asked for something the pass view cannot express: the whole picture. Three things,
all outside the pass's scope —

1. **A whole orbit, not the rise-to-set sliver.** The pass track is the few minutes the
   satellite is above the local horizon. The owner wants the full revolution wrapped around the
   globe.
2. **Where the satellite is right now**, moving, not only where tonight's pass will be.
3. **Every interesting satellite** — ISS, Tiangong/CSS, Hubble, a couple of bright ones — not
   the single next pass.

Handoff §13 said "display selected next pass, not every pass", and ADR-0025/0030 built exactly
that. This is a deliberate reversal of that scope, so it needs its own decision on record rather
than a renderer that quietly does more than the handoff sanctioned. "NUC calculates, Pi renders"
(§13) still holds and in fact does more work here.

## Decision

**A new event, `ambient.satellite_orbits`, carries a list of satellites, each with one full
orbital period of ground track.** `ambient.satellite_pass` is left exactly as it is — the single
next visible pass and its sky (az/el) geometry. The two are not merged: the pass's `track` is
azimuth/elevation *from the observer*, which only means anything while the satellite is up, and
overloading it with a whole orbit would make the same field mean two different things. A pass is
"which way do I look, and when"; an orbit is "where is it, and what loop does it trace". Different
questions, different events.

**Each orbit sample carries an ABSOLUTE UTC time, not an offset from a rise.** A pass track is
offsets from its rise because it is consumed within minutes of that rise. An orbit sits on the
wire for an hour between refreshes, and the client shows the *live* position by wrapping the real
clock into the period and interpolating along the track (`orbitAtTime`, extending
`groundAtFraction`). Absolute times are what make that work without the server re-publishing every
second — the marker advances on the wallboard's own clock. `sub_lat`/`sub_lng` + `as_of` give the
position at publish time so the tile can also show its age.

**`lat`/`lng` on an orbit sample are required**, unlike the pass track's optional ones. The pass
made them optional so an older producer that only knew a bearing could still send a pass; here the
ground track *is* the payload, so a producer that cannot compute a position emits no orbit for that
satellite rather than a sample without one.

**The set is chosen by NORAD catalog number and is configurable.** ISS (25544), Tiangong/CSS
(48274), Hubble (20580) and two bright NOAA birds by default, selected from CelesTrak's "visual"
(brightest-objects) group — both the id list and the TLE group are settings. Selection is by
number, not name, because CelesTrak names drift ("ISS (ZARYA)", "HST", "CSS (TIANHE)"). An id not
present in the fetched feed is skipped, not fatal. The orbit TLEs cache ~24 h like the pass TLEs,
so this adds no new hammering of CelesTrak, and computing a sub-satellite point needs no ephemeris
(unlike the pass's darkness/illumination check), so the extra job is cheap.

**The mock provider produces deterministic fake orbits** — inclined great circles drifting west as
Earth turns — so CI and hardware-free dev draw real loops with no network (the hardware-optional
rule, E-19).

**The client renders every satellite's full orbit and a live, colour-coded, labelled marker per
satellite**, on the same globe as the highlighted next pass, keeping the pass's text. The
orthographic projection has a useful property here: the antimeridian seam that forces a flat map
to special-case ±180° does not exist on a globe — `globePath` already breaks each loop at the limb
and draws only the near hemisphere. The wrap only needs care in *time* interpolation for the live
marker, which reuses `shortestLngDelta` via `groundAtFraction`.

## Consequences

- **Deployment ordering (ADR-0031): NUC at or before the door.** `ambient.satellite_orbits` is a
  brand-new event *type*. `StrictModel` + the discriminated union mean a consumer on older
  contracts rejects it (a `ValidationError`, not a silent ignore). The producer (`wallboard-worker`)
  and the first validator (`control-plane-api`'s `/ingest`) are both on the NUC and update as a unit;
  the door's `mqtt_bridge` must be at or after them, or it drops the event (logged, non-fatal — the
  door keeps working, the orbits tile just stays empty). A new type is in fact gentler than a new
  field: existing events keep parsing on an old consumer; only the new batch fails.
- The channel is now two events. A wallboard with the pass but not the orbits still renders exactly
  as before; the orbits are additive on the same globe. An older pass with no ground track no longer
  blanks the globe when orbits are present.
- The globe can get busy with five loops. Far-side culling (ADR-0030's silent-correctness risk) does
  double duty as decluttering — each loop shows only its near-hemisphere arc — and the colour legend
  keeps label↔satellite legible even when a marker is behind the globe.
- The live marker advances on the client's clock every second, so an hour-old payload is still
  truthful about position. The trade is that a satellite whose TLE has drifted since the last refresh
  is interpolated from slightly stale elements — acceptable at wallboard scale and refreshed hourly.
