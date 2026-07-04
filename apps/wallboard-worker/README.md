# wallboard-worker — control-plane background jobs

**Plane:** control · **Host:** NUC · **Language:** Python · **Tasks:** T-601, T-602, T-603, T-604, T-605 (adapters live in `integrations/`)

Scheduled/async jobs that keep ambient data fresh so the wallboard only ever reads cheap, pre-computed summaries. Nothing here is latency-critical; everything here is banned from the door Pi (ADR-0002).

## Jobs

| Job | Source | Output |
|---|---|---|
| Bird summary | BirdNET-Go on bird Pi | `ambient.bird_summary`; daily collage generation (async) |
| Satellite passes | Skyfield + CelesTrak TLE refresh (scheduled) | `ambient.satellite_pass` — next *selected* visible pass (elevation, darkness, illumination filters, observer coordinates from NUC-only config) |
| Aircraft | OpenSky (bounded box, cached, rate-limit-respecting) — later RTL-SDR/dump1090 | `ambient.aircraft_summary` |
| Printer | printer API adapter (read-only) | `ambient.printer_status` |
| Food | `FoodRecommendationProvider` interface (mock first) | `ambient.food_recommendation` daily |

Results are written to Postgres + pushed over MQTT/HTTP so control-plane-api can serve them; the Pi's door-api caches the latest bundle for NUC-outage fallback.

## Rules

- Every job runs behind a feature flag (`FEATURE_*`) and an adapter interface with a mock implementation.
- Job failure degrades one tile, never the system; retries with backoff; stale data is labeled with `as_of`.
- Do not promise data a source doesn't provide (e.g., aircraft route/destination on OpenSky).
- API credentials/keys are NUC-only secrets.
