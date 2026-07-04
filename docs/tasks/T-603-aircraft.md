# T-603: Aircraft adapter (OpenSky MVP)

**Agent:** gemini · **Milestone:** M6 · **Depends on:** T-501

## Context

Spec: [integrations/aircraft/README.md](../../integrations/aircraft/README.md). API-based MVP; local RTL-SDR receiver is a separate future brief.

## Deliverables

- `AircraftProvider`: `opensky` implementation — bounded-box query around configured area, hard-capped poll rate honoring anonymous/registered quotas, aggressive caching (serve cached on 429/failure), distance/heading derivation; `mock` implementation.
- `ambient.aircraft_summary` emission from wallboard-worker; only fields the source provides (callsign, altitude, distance, heading) — no fabricated route/destination.
- Credentials (optional registered account) NUC-only config.
- Fixture-based tests including quota-exceeded and empty-sky responses.

## Out of scope

dump1090/RTL-SDR (future brief against the same interface), tile design, historical tracking (we display "now", we don't store trajectories).

## Acceptance criteria

- Sustained run stays under the configured quota (request-count test with mocked clock).
- API outage → cached data + staleness marker; recovery automatic.
- Summary contents verified against fixtures; no extra fields invented.
