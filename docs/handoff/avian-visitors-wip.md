# AvianVisitors integration checkpoint (completed)

Checkpoint date: 2026-07-13 (Asia/Singapore)
Completion date: 2026-07-14 (Asia/Singapore)

This document records the saved WIP state at commit `3be8861`. The completion
agent finished the checklist below; it is retained as implementation history,
not as an active resume brief.

## User request

Replace or supplement the current BirdNET-Go bird integration with
<https://github.com/Twarner491/AvianVisitors>, while keeping it compatible with
the complete Doorboard hardware inventory. The implementation must not add any
dependency to the door critical path.

## Git checkpoint

- Doorboard branch: `task/T-601-avian-visitors`
- Branch base: `origin/main` at `ce01e4e`
- Upstream AvianVisitors branch reviewed: `avian-visitors`
- Exact upstream commit reviewed and selected for pinning:
  `1b33a3cbc4f3b1fe0f9987e2a381ef970283931f`
- A temporary upstream clone exists at `/tmp/AvianVisitors`; it is reference
  material only and must never be copied wholesale into Doorboard.

Commit `3be8861` was intentionally a WIP checkpoint and was not merge-ready.
The deployment, Compose, documentation, lint, type-check, and full-test work
listed below has since been completed on the same branch.

## Required project context

Before continuing, read these files in full, as required by `AGENTS.md`:

1. `ARCHITECTURE.md`, especially sections 1, 4, 9, and 10.
2. `docs/tasks/T-601-birdnet-adapter.md`.
3. `integrations/birdnet/README.md` and `deploy/pi-bird/README.md`.
4. `CONTRIBUTING.md`.

The T-601 `Out of scope` section is binding. In particular, do not put any of
this on the door Pi and do not add custom raw-audio processing. Upstream's own
retention controls may be configured as part of deployment.

## Architecture decision

Run AvianVisitors only on the dedicated spare Pi 4 near the bird microphone.
The NUC-hosted `wallboard-worker` polls AvianVisitors' LAN-only, read-only PHP
API and posts the existing `ambient.bird_summary` contract. The following
hardware stays completely outside this integration:

- Door Pi 5, Hailo-8 AI HAT+, cameras, and door SSD
- ESP32-S3
- NAS
- Optional ADS-B Pi 3/4

This preserves `button -> ESP32 feedback -> local UI` with no bird-Pi, NUC,
NAS, or internet dependency. Laptop and CI operation use `MockBirdProvider`.

Upstream advertises Pi 4B, Pi 5, Pi 3A+, and Pi Zero 2W support, but requires a
64-bit OS. Doorboard should document Pi 4 as the assigned/preferred host. Do
not run AvianVisitors on the door Pi 5 merely because upstream supports it.

## Upstream contract findings

The native endpoint is:

`GET /avian/api/birdnet-api.php?action=recent&hours=N`

It returns:

```json
{
  "hours": 24,
  "species": [
    {
      "sci": "Haemorhous mexicanus",
      "com": "House Finch",
      "n": 4,
      "best_conf": 0.91,
      "last_seen": "2026-07-12 17:30:01",
      "top_file": "...",
      "top_at": "..."
    }
  ],
  "as_of": "2026-07-12T18:00:00-07:00"
}
```

The endpoint is species-collapsed. It provides count and best confidence, not
individual detections or average confidence. Doorboard's existing contract
calls the field `confidence_avg`; the adapter explicitly maps upstream
`best_conf` into that field and documents the limitation. Do not invent a new
event or field without the contracts escalation process.

The default upstream LAN deployment has no API authentication. The Doorboard
adapter supports optional Caddy Basic Auth. Production docs should recommend a
LAN firewall rule that permits only the NUC, plus Basic Auth where configured.
Do not enable upstream Cloudflare forwarding, MQTT bridge, Home Assistant
forwarder, Gemini image generation, or other cloud paths for this integration.

The upstream license is CC-BY-NC-SA-4.0 and non-commercial-only. Preserve that
warning in deployment documentation.

## Completed code

### Bird provider

`integrations/birdnet/src/birdnet/provider.py` now includes:

- Strict `AvianVisitorsConfig` with threshold, species filters, rolling hours,
  optional Basic Auth, timeout, response-size bound, and species-row bound.
- Auth-pair validation: username and password must be configured together.
- `AvianVisitorsProvider` using the native read-only API.
- Streaming response reads capped before the complete response can accumulate.
- Strict response validation for counts, confidence, timestamps, requested
  window, and bounded row count.
- Case-insensitive common/scientific-name filters.
- Failure conversion to `RuntimeError` so the existing stale-data path is used.
- Deterministic summary ordering.
- Optional `httpx.BaseTransport` injection for hardware-free tests.

The new config/provider are exported from `integrations/birdnet/src/birdnet/__init__.py`.

### Worker configuration and provider selection

`apps/wallboard-worker/src/wallboard_worker/settings.py` now includes:

- `BIRD_PROVIDER=birdnet_go|avian_visitors|mock`
- `AVIAN_VISITORS_URL`
- `AVIAN_VISITORS_RECENT_HOURS`
- `AVIAN_VISITORS_BASIC_USER`
- `AVIAN_VISITORS_BASIC_PASSWORD`
- `AVIAN_VISITORS_TIMEOUT_S`
- Scheduler intervals, heartbeat path, and pre-issued ingest token support
- Validation that enabled jobs have ingest authentication

`apps/wallboard-worker/src/wallboard_worker/providers.py` is the shared factory
used by the CLI and scheduler. Legacy BirdNET-Go and mock behavior remain
available.

### Real worker runtime

`apps/wallboard-worker/src/wallboard_worker/scheduler.py` adds a monotonic,
failure-isolated scheduler. It registers only enabled features, keeps one bad
job from killing other jobs, and updates a heartbeat file. The CLI now has
`wallboard-worker run [--once] [--mock]`, and the package declares the console
script in `pyproject.toml`.

`get_ingest_token()` now reuses `WALLBOARD_WORKER_INGEST_TOKEN` and caches the
admin-token bootstrap result for the process lifetime.

### Tests

Recorded API fixture:

`apps/wallboard-worker/tests/fixtures/avian_recent.json`

Tests cover:

- Native path/query and Basic Auth
- Recorded response mapping and common/scientific species filtering
- HTTP 503/unreachable behavior
- Structurally invalid and well-formed-but-invalid JSON
- Oversized responses and excessive row counts
- Mismatched response window
- Partial Basic Auth configuration
- Provider factory selection
- Scheduler job isolation and heartbeat
- Enabled-job registration
- Process-scoped ingest token reuse

Last focused verification:

```text
UV_CACHE_DIR=/tmp/doorboard-uv-cache uv run pytest \
  apps/wallboard-worker/tests/test_birdnet.py \
  apps/wallboard-worker/tests/test_scheduler.py \
  apps/wallboard-worker/tests/test_food_recommendation.py

21 passed in 0.16s
```

`git diff --check` also passed before this handoff file was added.

## Completion checklist (completed 2026-07-14)

1. Add a fresh-install-only `deploy/pi-bird/install-avian-visitors.sh`.
   Fetch the exact upstream commit above, verify the checked-out SHA, and run
   `scripts/install_birdnet.sh` directly. Do not use upstream's `curl | bash`,
   do not follow branch HEAD, and do not reboot automatically.
2. Require an explicit marker such as `DOORBOARD_BIRD_NODE=1` so the installer
   is not accidentally run on the door Pi. Require non-root execution,
   passwordless sudo, aarch64 or x86_64, and a supported 64-bit Bookworm/newer
   environment. The intended hardware remains the spare Pi 4.
3. Require explicit latitude/longitude and update `/etc/birdnet/birdnet.conf`
   after upstream install. Suggested defaults: `CONFIDENCE=0.70`,
   `PRIVACY_THRESHOLD=1`, `FULL_DISK=purge`, `PURGE_THRESHOLD=80`, and
   `MAX_FILES_SPECIES=25` (upstream preserves the newest seven days separately).
   This uses upstream cleanup and stays inside the raw-audio out-of-scope fence.
4. Remove upstream's weekly `update_birdnet.sh -a` entry from `/etc/crontab`.
   Otherwise the pinned deployment silently becomes unpinned. Keep upstream's
   built-in `disk_check.sh` and `disk_species_clean.sh` cleanup entries.
5. Add `deploy/pi-bird/verify-avian-visitors.sh` or equivalent documented checks
   for USB mic discovery, required services, exact Git SHA, and valid recent API
   JSON after the operator performs the one required reboot.
6. Rewrite `deploy/pi-bird/README.md` for AvianVisitors, assigned hardware,
   64-bit OS, microphone, network isolation, retention controls, license,
   install/verify commands, upgrade process, and rollback. Preserve a short
   legacy BirdNET-Go provider note if useful.
7. Update `integrations/birdnet/README.md` and `apps/wallboard-worker/README.md`
   with provider semantics, confidence limitation, degraded behavior, and all
   environment variables.
8. Update `.env.example` with `WALLBOARD_WORKER_INGEST_TOKEN`, `BIRD_PROVIDER`,
   Avian URL/hours/auth/timeout settings, and the legacy BirdNET-Go URL.
9. Replace the placeholder worker container in
   `infra/compose/docker/wallboard-worker.Dockerfile`: copy `integrations`, use
   `uv sync --no-editable`, run `wallboard-worker run`, and healthcheck the
   scheduler heartbeat. A known working reference is commit `aba127b` on the
   separate `task/T-405-kiosk-ui-redesign` branch; copy only the relevant
   worker/Compose changes, not unrelated UI work.
10. Activate and configure `wallboard-worker` in both Compose files. Production
    must pass the pre-issued ingest token, `BIRD_PROVIDER`, Avian URL/auth, and
    feature flags. Dev should run all jobs with `--mock` and an admin bootstrap
    token. Keep the worker NUC-only.
11. Run formatting/lint/type checks and the entire wallboard-worker suite. The
    new settings validator may reveal older tests that enable a feature without
    an ingest token; add a test token only where appropriate.
12. Run the full Python test suite, Compose config validation (if Docker is
    available), and the repository's static credential/critical-path checks.
    Never set `PYTHONPATH`.

## Recommended verification commands

```bash
UV_CACHE_DIR=/tmp/doorboard-uv-cache uv run ruff format --check \
  integrations/birdnet apps/wallboard-worker
UV_CACHE_DIR=/tmp/doorboard-uv-cache uv run ruff check \
  integrations/birdnet apps/wallboard-worker
UV_CACHE_DIR=/tmp/doorboard-uv-cache uv run pyright \
  integrations/birdnet apps/wallboard-worker
UV_CACHE_DIR=/tmp/doorboard-uv-cache uv run pytest apps/wallboard-worker/tests
UV_CACHE_DIR=/tmp/doorboard-uv-cache uv run pytest
git diff --check
```

If dependency downloads fail in the sandbox, rerun `uv` with approved network
access. Do not work around import failures with `PYTHONPATH`.
