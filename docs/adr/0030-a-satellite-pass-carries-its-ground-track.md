# ADR-0030: a satellite pass carries its ground track

**Status:** Accepted · **Date:** 2026-08-18

Extends `ambient.satellite_pass`, additively. Narrows the renderer chosen in
[ADR-0025](0025-satellite-pass-geometry.md); that ADR's payload additions all remain.

## Context

ADR-0025 put the pass's *sky* geometry on the wire — azimuth and elevation over time — and
drew it as a horizon-at-the-rim polar dome. That answers "which way do I look", which is the
right question during a pass and a fairly abstract one for the other twenty-three hours.

The owner asked for something different: a globe, with the tracked satellite pinging where it
actually is, and where and when its path passes campus.

That cannot be derived from what ADR-0025 added. Azimuth and elevation are *from the observer*;
converting a bearing and an angle into a position on Earth needs the orbit, which the wallboard
does not have and should not acquire. The provider, however, already had it — `_sample_track`
walks the pass moment by moment with Skyfield to get alt/az, and Skyfield will give the
sub-satellite point for the same moment in one more call.

## Decision

**`SatelliteTrackSample` carries `lat`/`lng`: the sub-satellite point.** Computed from the same
moment as that sample's alt/az, so the two cannot disagree about where the satellite was.

Both optional. A producer from before this change omits them, and the globe then has nothing to
plot: it says the pass carries no ground track rather than inventing a position from a bearing.
`groundTrack()` filters to the samples that actually carry a position, so a partially populated
track degrades to the points it has.

**The satellite channel is a globe, orthographic, centred on the door.** The sky dome is
removed rather than kept alongside — two renderings of one pass is a maintenance cost with no
reader benefit, and the dome's information (direction, max elevation) survives as text in the
globe's stat panel. `skyDome.ts` was reduced to the two helpers that are about *time* rather
than dome geometry and renamed `passTiming.ts`; the projection and its tests went with the
panel.

**Coastlines are baked, not fetched.** `scripts/build-world-coastline` bundles Natural Earth
110m, simplified and delta-encoded, at 18 KB for the whole world. Same reasoning as the campus
streets: the door works with the internet down (E-19), and a wallboard is not where you want to
discover a tile server is unreachable.

**"Closest approach", not "intersection".** A pass does not cross a point, so the panel reports
the nearest the ground track comes to the door and when — a distance and a time, both true —
rather than claiming an intersection that did not happen.

## Consequences

- **Far-side culling is the correctness risk, and it is silent.** In an orthographic
  projection half the sphere is behind the globe, and a far-side point still projects to a
  plausible position on the near face. Unculled, Asia draws over the Pacific and the satellite
  appears on the wrong side of the world with nothing looking broken. `globe.ts` returns
  `visible` per point and `globePath` starts a new subpath at every limb crossing — joining
  across one would draw a chord over the globe's face. Both are unit-tested, including a case
  where the line is entirely behind.
- Longitude interpolation takes the short way, so a pass crossing the antimeridian does not
  sweep backwards around the planet.
- The ping animation stops under `prefers-reduced-motion`, and the preview sweep runs only when
  no pass is in progress: an animation nobody is watching should cost a 24/7 wallboard nothing.
- The provider does one extra Skyfield call per sampled point. The job runs hourly.
