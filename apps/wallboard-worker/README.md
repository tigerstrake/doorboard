# wallboard-worker — control-plane background jobs

**Plane:** control · **Host:** NUC · **Language:** Python · **Tasks:** T-601, T-602, T-603, T-604, T-605 (adapters live in `integrations/`)

Scheduled/async jobs that keep ambient data fresh so the wallboard only ever reads cheap, pre-computed summaries. Nothing here is latency-critical; everything here is banned from the door Pi (ADR-0002).

## Jobs

| Job | Source | Output |
|---|---|---|
| Bird summary | BirdNET-Go on bird Pi | `ambient.bird_summary`. The daily collage is **not implemented** here — the wallboard's bird tile shows an image the bird Pi serves (`VITE_BIRD_COLLAGE_URL`), and a stub that logged "executed successfully" every day has been removed. |
| Satellite passes | Skyfield + CelesTrak TLE refresh (scheduled) | `ambient.satellite_pass` — next *selected* visible pass (elevation, darkness, illumination filters, observer coordinates from NUC-only config) |
| Aircraft | OpenSky (bounded box, cached, rate-limit-respecting) — later RTL-SDR/dump1090 | `ambient.aircraft_summary` |
| Printer | printer API adapter (read-only) | `ambient.printer_status` |
| Food | `FoodRecommendationProvider` interface (mock first) | `ambient.food_recommendation` every 3 h, so the pick follows the meal window |

Results are written to Postgres + pushed over MQTT/HTTP so control-plane-api can serve them; the Pi's door-api caches the latest bundle for NUC-outage fallback.

## Rules

- Every job runs behind a feature flag (`FEATURE_*`) and an adapter interface with a mock implementation.
- Job failure degrades one tile, never the system; retries with backoff; stale data is labeled with `as_of`.
- Do not promise data a source doesn't provide (e.g., aircraft route/destination on OpenSky).
- API credentials/keys are NUC-only secrets.

## Runtime

`wallboard-worker run` starts the monotonic scheduler. Only enabled jobs are
registered; one job failure is logged and does not stop the others. Each run
executes on the job's own thread under a deadline (`WALLBOARD_JOB_TIMEOUT_S`,
default 60 s; the food job has `WALLBOARD_FOOD_JOB_TIMEOUT_S`, default 300 s):
a run that overruns is abandoned, logged as `scheduled_job_timed_out`, and
backed off like a failure, and the job is not retried while that run is still
going. Production uses a pre-issued `WALLBOARD_WORKER_INGEST_TOKEN`, which is
reused for the life of the process. `CONTROL_PLANE_ADMIN_TOKEN` is accepted only
as a dev bootstrap fallback and is not passed to the production container.

Logs are one JSON object per line on stdout (`doorboard_observability`, the
same formatter every service uses), so the `job`, `duration_s` and `retry_in_s`
fields the scheduler attaches actually appear. `SIGTERM` -- what `docker stop`
sends -- asks the scheduler to stop: the current pass finishes, the executors
are closed, `scheduler_stopped` is logged, and the process exits.
