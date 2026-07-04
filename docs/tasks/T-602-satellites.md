# T-602: Satellite pass prediction

**Agent:** gemini · **Milestone:** M6 · **Depends on:** T-501

## Context

Spec: [integrations/satellites/README.md](../../integrations/satellites/README.md). Skyfield + CelesTrak on the NUC; the Pi only renders (handoff §13).

## Deliverables

- `SatelliteProvider`: `skyfield` implementation — scheduled TLE refresh (cached, respectful fetch frequency, stale > 7 days = degraded), visible-pass computation (min elevation, observer darkness, satellite illumination) for a configurable watchlist with ISS default; `mock` implementation.
- Selection logic: emit the next *selected* visible pass as `ambient.satellite_pass` — never a full pass dump.
- Observer coordinates from NUC-only config; never emitted in any event or log (test-enforced).
- Deterministic unit tests with pinned TLE fixtures + fixed times (no network, no wall clock).
- wallboard-worker job wiring + feature flag.

## Out of scope

Tile visuals, aircraft (T-603), any door-Pi computation.

## Acceptance criteria

- Fixture-based tests reproduce known pass predictions (compare against Skyfield-documented examples within tolerance).
- Stale-TLE and fetch-failure paths degrade to staleness marker, tested.
- Grep/test proves coordinates absent from events, logs, and bundles.
