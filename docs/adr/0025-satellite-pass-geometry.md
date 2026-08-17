# ADR-0025: a satellite pass carries its geometry, not just a compass letter

**Status:** Accepted · **Date:** 2026-08-17

Extends `ambient.satellite_pass` (T-325). Additive only; no existing field changes meaning.
Written after the fact — the T-325 commit and the code comments cited this ADR before it
existed, which is the discipline failure CLAUDE.md §4 exists to prevent.

## Context

`ambient.satellite_pass` said `direction: "NW"` and `max_elevation_deg: 64.5`. The provider
computes a pass by finding its rise, culmination and set events with Skyfield — it has the
whole arc in hand — and then reduced it to one compass point before publishing. So the
wallboard could say "NW, 64°" and could not say where to look, when to look, or for how
long, because that information had been computed and thrown away upstream.

A pass is a *direction over time*. The flights channel plots aircraft on a ground map, which
is right for aircraft and wrong here: a satellite's position on the ground is not what you
point yourself at.

## Decision

**The payload carries the pass's geometry: `set_at`, the rise/culmination/set azimuths, and
a sampled `track` of `(t_offset_s, azimuth_deg, elevation_deg)`.** The producer already had
all of it. Nothing is derived on the consumer side, because deriving an arc from three
numbers means inventing a shape.

**Every geometry field is optional on the Python side, and the consumer must treat it as
absent-able.** An older producer, a cached pass from before this change, or the offline mock
may carry none of it. The renderer's fallback is not an approximation — it plots the single
point it genuinely knows (the culmination) and says on screen that it is showing the high
point only. A panel must not assert an arc it was not given.

`track` serialises to `[]` rather than being omitted, so the generated TypeScript type has
it as required and consumers can index it without a null check. "Present but empty" and
"absent" mean the same thing to a reader — no track — and collapsing them removes a case
rather than adding one.

**The renderer is a horizon-at-the-rim, zenith-at-the-centre polar plot, north up.** The
conventional sky chart, i.e. the picture you would hold over your head. The projection lives
in `apps/door-ui/src/wallboard/skyDome.ts`, DOM-free and unit-tested, because a sign error
in it produces a confident instruction to look the opposite way — wrong, plausible, and
untestable through the rendered component.

**The marker animates on real elapsed time during a pass, and on a preview loop otherwise.**
An ambient screen showing a satellite genuinely overhead must show where it actually is. The
preview loop only runs when no pass is in progress, and stops when the panel is not showing.

## Consequences

- The provider must sample the arc, bounded. Consumers may not assume a fixed sample count
  or even spacing; the projection interpolates between whatever samples arrive.
- Azimuth interpolation takes the short way around north: 350° → 10° is a 20° sweep, not
  340° backwards across the whole sky. Tested, because getting it wrong draws the arc
  through the wrong two-thirds of the dome.
- The mock fixture now carries a synthetic arc. Without one, mock mode could only ever
  render the "high point only" fallback — mock mode has to exercise the panel's actual
  behaviour, not just its degraded path.
- `SatelliteFocusPanel`, `SkyCompass` and the compass-bearing table it needed are gone. The
  16-point rose existed only to aim a needle from a direction *label*; with real azimuths
  the label is for prose, not for geometry.
