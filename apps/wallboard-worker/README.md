# wallboard-worker — control-plane background jobs

**Plane:** control · **Host:** NUC only · **Language:** Python · **Tasks:**
T-601–T-605 (adapters live in `integrations/`)

The worker schedules ambient-data jobs and posts existing typed events to
`control-plane-api`. It is latency-tolerant and banned from the door Pi. Laptop
and CI runs use adapter mocks, so no bird Pi, camera, SDR, printer, or other
hardware is required.

Run the long-lived scheduler with `wallboard-worker run`; `--once` executes each
enabled job once, and `--mock` forces every hardware/network provider behind an
available mock. Each job is failure-isolated and scheduled with a monotonic
clock. The heartbeat file is refreshed by the scheduler and used only for
container liveness.

## Jobs and degradation

| Job | Source | Output |
|---|---|---|
| Bird summary | AvianVisitors or legacy BirdNET-Go on the dedicated bird Pi | `ambient.bird_summary` plus daily-collage stub |
| Satellite passes | Skyfield + cached CelesTrak TLE | `ambient.satellite_pass` |
| Aircraft | OpenSky; later an optional ADS-B adapter | `ambient.aircraft_summary` |
| Printer | Read-only printer API | `ambient.printer_status` |
| Food | `FoodRecommendationProvider` | `ambient.food_recommendation` |

Provider or ingest failure never terminates the scheduler or another job. Bird
failures post no new event, leaving the last control-plane value to become
stale. The AvianVisitors API exposes only per-species count and best confidence,
so `best_conf` is mapped to the existing `confidence_avg` contract field; it is
not an average. `AVIAN_VISITORS_RECENT_HOURS` is rolling even though the fixed
contract field remains `window="today"`.

Production uses a pre-issued, ingest-scoped
`WALLBOARD_WORKER_INGEST_TOKEN`. `CONTROL_PLANE_ADMIN_TOKEN` is an accepted
development bootstrap only: the worker issues one process-scoped ingest token
and reuses it rather than calling the admin endpoint for every job.

## Environment variables

All values below are optional unless an enabled production job requires the
pre-issued ingest token. Defaults are shown in parentheses.

### Runtime and schedules

| Variable | Meaning |
|---|---|
| `DOORBOARD_DOOR_ID` | Event door ID (`primary`) |
| `CONTROL_PLANE_URL` | Ingest API base URL (`http://127.0.0.1:8090`) |
| `WALLBOARD_WORKER_INGEST_TOKEN` | Production ingest-scoped token (empty) |
| `CONTROL_PLANE_ADMIN_TOKEN` | Dev-only token bootstrap (empty) |
| `WALLBOARD_WORKER_HEARTBEAT_PATH` | Scheduler heartbeat (`/tmp/wallboard-worker-heartbeat`) |
| `WALLBOARD_BIRD_INTERVAL_S` | Bird summary interval (`300`) |
| `WALLBOARD_COLLAGE_INTERVAL_S` | Collage-stub interval (`86400`) |
| `WALLBOARD_SATELLITE_INTERVAL_S` | Satellite interval (`3600`) |
| `WALLBOARD_AIRCRAFT_INTERVAL_S` | Aircraft interval (`30`) |
| `WALLBOARD_PRINTER_INTERVAL_S` | Printer interval (`30`) |
| `WALLBOARD_FOOD_INTERVAL_S` | Food interval (`86400`) |

### Feature flags and bird providers

| Variable | Meaning |
|---|---|
| `FEATURE_BIRDNET` | Enable bird summary and collage jobs (`false`) |
| `FEATURE_SATELLITES` | Enable satellite job (`false`) |
| `FEATURE_AIRCRAFT` | Enable aircraft job (`false`) |
| `FEATURE_PRINTER` | Enable printer job (`false`) |
| `FEATURE_FOOD` | Enable food job (`false`) |
| `BIRD_PROVIDER` | `avian_visitors`, `birdnet_go`, or `mock` (`birdnet_go` standalone; Compose defaults production to `avian_visitors`) |
| `BIRDNET_CONFIDENCE_THRESHOLD` | Accepted confidence floor (`0.70`) |
| `BIRDNET_SPECIES_FILTER` | Comma-separated common/scientific allowlist (empty) |
| `AVIAN_VISITORS_URL` | AvianVisitors base URL (`http://birdnet.local`) |
| `AVIAN_VISITORS_RECENT_HOURS` | Rolling API window, 1–168 (`24`) |
| `AVIAN_VISITORS_BASIC_USER` | Optional Caddy Basic Auth user (empty) |
| `AVIAN_VISITORS_BASIC_PASSWORD` | Matching Basic Auth password (empty) |
| `AVIAN_VISITORS_TIMEOUT_S` | Request timeout, >0 and <=30 (`5`) |
| `BIRDNET_URL` | Legacy BirdNET-Go base URL (`http://127.0.0.1:8080`) |

The root `.env.example` calls the legacy host variable `BIRDNET_GO_URL`;
production Compose maps it to the worker's `BIRDNET_URL` setting.

### Other adapters

| Variable | Meaning |
|---|---|
| `SATELLITES_WATCHLIST` | Comma-separated satellite names (`ISS (ZARYA)`) |
| `SATELLITES_OBSERVER_LAT` / `SATELLITES_OBSERVER_LON` | NUC-only observer coordinates (`0`) |
| `SATELLITES_OBSERVER_ELEVATION` | Observer metres above sea level (`0`) |
| `SATELLITES_MIN_ELEVATION` | Minimum visible elevation degrees (`10`) |
| `SATELLITES_TLE_URL` | TLE source (CelesTrak stations endpoint) |
| `SATELLITES_TLE_CACHE_PATH` | Local TLE cache (`/tmp/satellite_tle_cache.txt`) |
| `OPENSKY_USERNAME` / `OPENSKY_PASSWORD` | Optional OpenSky credentials (empty) |
| `AIRCRAFT_OBSERVER_LAT` / `AIRCRAFT_OBSERVER_LON` | NUC-only aircraft observer coordinates (`0`) |
| `AIRCRAFT_BBOX_HALF_SIZE_LAT` / `AIRCRAFT_BBOX_HALF_SIZE_LON` | Query half-width degrees (`0.25`) |
| `AIRCRAFT_POLL_COOLDOWN_SECONDS` | OpenSky cache/rate interval (`30`) |
| `OCTOPRINT_URL` | Printer API URL (`http://octopi.local`) |
| `OCTOPRINT_API_KEY` | Read-only printer key (empty) |
| `PRINTER_CAMERA_STREAM_URL` | Optional printer stream URL (empty) |
| `FOOD_RECOMMENDATION_CACHE_PATH` | Fallback cache (`/tmp/doorboard_food_recommendation_cache.json`) |

Secrets belong only in the NUC's uncommitted `.env`; the worker container is
part of the NUC Compose stack and must never be deployed on the door Pi.
