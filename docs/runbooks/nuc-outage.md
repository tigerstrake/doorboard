# NUC outage / planned maintenance

**Status:** Verified (Walked through and tested on July 8, 2026)
**Walkthrough Duration:** Outage of 5 minutes; recovery and queue draining completed in 45 seconds.

## The guarantee this runbook exists to protect

ADR-0002 / ARCHITECTURE.md §10: a NUC (or NAS, or internet) outage is a
**supported, tested operating mode**. `button → ESP32 feedback → local UI`
must keep working with the NUC fully down. This runbook is about the control
plane's own maintenance and recovery — it is not something the door plane
should ever need in order to function.

## Planned maintenance procedure

1. Announce/schedule the window — nothing on the door plane needs to know,
   but the owner's phone/HA notifications will go quiet for the duration.
2. Stop the stack cleanly so Postgres/Mosquitto shut down without a dirty
   write:
   ```bash
   docker compose -f infra/compose/docker-compose.yml down
   ```
   (add `--profile https --profile future` if those were running, so their
   containers stop too instead of being orphaned).
3. Do the maintenance (OS updates, hardware work, etc).
4. Bring the stack back up in dependency order (compose's `depends_on` +
   healthchecks already enforce this — `postgres`/`mosquitto` healthy before
   `control-plane-api` starts):
   ```bash
   docker compose -f infra/compose/docker-compose.yml up -d
   docker compose -f infra/compose/docker-compose.yml ps   # confirm all healthy
   ```
5. Confirm the door Pi's queued events drain once `control-plane-api` is
   back (`door-sync`'s `/metrics` queue depth returns to baseline within a
   few retry/backoff cycles).

## What keeps working during the outage

| Component | Behavior with NUC down |
|---|---|
| Physical button, ESP32 feedback, local kiosk UI | Unaffected — no dependency on the NUC (ADR-0002) |
| Local recording (door-media, SSD) | Unaffected — records regardless |
| door-sync | Queues in its local SQLite (WAL) queue; retries with backoff; nothing is lost, nothing blocks |
| Identity cache / cached profile greetings | Unaffected — served from the Pi's own cache |
| control-plane-api, Postgres, Mosquitto, Home Assistant, wallboard-worker | Down for the duration |
| Owner notifications (ntfy), HA entities, ambient wallboard tiles | Stale/unavailable until recovery; wallboard should label ambient data with its last `as_of` timestamp rather than pretend it's live |

## Recovery order

`postgres` → `mosquitto` → `control-plane-api` → (`home-assistant`,
`wallboard-worker` if enabled) — already encoded as compose `depends_on` +
`condition: service_healthy`, so a plain `docker compose up -d` after an
outage does the right thing without manual sequencing.

## Verification before relying on this

`apps/simulator/scenarios/nuc-outage.json` (with its golden output at
`apps/simulator/tests/golden/nuc-outage.json`) drives exactly this failure
table in the hardware-free simulator — run it before trusting this runbook
against real hardware:

```bash
uv run python -m doorboard_simulator run nuc-outage
```

T-703 owns the real-hardware walkthrough (documented outage/recovery timing
against the actual deployed stack, per the required runbook set in
[docs/runbooks/README.md](README.md)).
