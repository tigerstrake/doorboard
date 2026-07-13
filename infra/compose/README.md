# infra/compose

Docker Compose files for the NUC control plane (T-503). Two stacks:

| File | Purpose |
|---|---|
| [`docker-compose.yml`](docker-compose.yml) | Production NUC stack: PostgreSQL, Mosquitto, control-plane-api, `wallboard-worker`, Home Assistant, an optional Caddy, and a Postgres backup job |
| [`compose.dev.yml`](compose.dev.yml) | Laptop dev stack: the same control plane plus `doorboard-simulator` standing in for the entire door plane (ESP32/cameras/Hailo — see [apps/simulator/README.md](../../apps/simulator/README.md)) and a local `ntfy` container, so the full bell→notification path is demoable with zero real hardware and no internet accounts |

Secrets live in `.env` at the repo root (never in git — see `.env.example`
and handoff §16). Both files read it via `--env-file .env`.

## Prerequisites

- Docker Engine + Docker Compose v2 (`docker compose version`).
- A copy of `.env` derived from the repo-root `.env.example` with every NUC
  variable filled in (`POSTGRES_PASSWORD`, `MQTT_*_PASSWORD`,
  `CONTROL_PLANE_ADMIN_TOKEN`, `NAS_BACKUP_PATH`, …). Generate random
  passwords rather than reusing the placeholder text.
- Production only: the NAS backups share already mounted on the host at
  whatever path `NAS_BACKUP_PATH` points to (share creation itself is
  `deploy/nas` — owner/manual, out of scope here).

## Running it

Production, from the repo root:

```bash
docker compose -f infra/compose/docker-compose.yml --env-file .env up -d
docker compose -f infra/compose/docker-compose.yml ps        # all healthy?
```

On a fresh NUC, first follow the two-stage token bootstrap in
[deploy/nuc/README.md](../../deploy/nuc/README.md). All worker features default
off so the control plane can issue its dedicated ingest token before any worker
job is enabled; enabling a job without that token makes the worker fail closed.

Optional internal HTTPS remains behind a
[Compose profile](https://docs.docker.com/compose/how-tos/profiles/):

```bash
docker compose -f infra/compose/docker-compose.yml --profile https up -d
```

Laptop dev stack:

```bash
docker compose -f infra/compose/compose.dev.yml --env-file .env up -d
infra/compose/scripts/demo-bell-to-ha.sh   # walks the bell -> HA -> notify path
docker compose -f infra/compose/compose.dev.yml down -v   # tear down + wipe volumes
```

## wallboard-worker

The worker is an active NUC-only service. Every enabled production job requires
a pre-issued `WALLBOARD_WORKER_INGEST_TOKEN`; feature flags determine which
jobs run. The
AvianVisitors bird provider polls only the dedicated bird Pi over the LAN.
The laptop stack enables every job with `--mock` and uses its dev admin token
to bootstrap one process-scoped ingest token. Its heartbeat healthcheck proves
the scheduler loop is alive even when a provider is unavailable; job failures
remain isolated. See
[apps/wallboard-worker/README.md](../../apps/wallboard-worker/README.md) for the
complete environment contract.

## Mosquitto: auth and per-device ACLs

No anonymous connections (`allow_anonymous false`). Every client
(`control-plane-api`, `door-pi`, `home-assistant`, `ha-discovery`,
`healthcheck`) has its own credential and is confined to its own topics —
see [`mosquitto/acl.conf`](mosquitto/acl.conf) for the exact scoping and
its comments for the trust-model rationale. Passwords are generated inside
the container at startup from `.env` by
[`mosquitto/entrypoint.sh`](mosquitto/entrypoint.sh) via `mosquitto_passwd`
— `mosquitto.conf`/`acl.conf` themselves are static and secret-free, safe to
commit.

`tests/integration/test_mosquitto_acl.py` starts the real image with the
real config and proves both halves of the acceptance criterion against a
running broker: an anonymous connection is refused, and the `door-pi`
credential's publishes outside `doorboard/door/#` never reach a subscriber
(silently dropped — MQTT has no reject path for a QoS 0 ACL denial, so the
test asserts on delivery, not on the publisher's own return code). It skips
if Docker isn't available; CI has Docker.

**LAN-only exposure is a network fact, not something Compose enforces.**
Port bindings default to `0.0.0.0` (every interface) for the common
single-NIC-NUC case; set `LAN_BIND_ADDR` in `.env` to a specific address on
a dual-homed box. Either way: put the NUC on an isolated VLAN if your router
supports one, and never port-forward 1883/8090/8123/443 to the internet
(handoff §16).

## Home Assistant bridge

See [integrations/home-assistant/README.md](../../integrations/home-assistant/README.md)
for the MQTT discovery entities, the inbound-webhook automation, and the
setup doc. HA's own runtime state lives in the `ha_data` named volume, never
in this repo — only the static YAML files under
`integrations/home-assistant/config/` are bind-mounted (read-only) into it.

## Optional internal HTTPS (Caddy)

Disabled by default. Enable with `--profile https` once `DOORBOARD_INTERNAL_DOMAIN`
is set in `.env` to a LAN-only hostname (never a public one). See
[infra/caddy/README.md](../caddy/README.md).

## Postgres backups

`postgres-backup` runs [`backup/pg-backup.sh`](backup/pg-backup.sh) on a
loop, dumping to the NAS-mounted `NAS_BACKUP_PATH` (never a Docker volume —
a volume wipe must not be able to take the only backup with it, per
ADR-0007). Restore steps: [docs/runbooks/nas-backup-restore.md](../../docs/runbooks/nas-backup-restore.md).
`tests/integration/test_postgres_backup_restore.py` proves the exact
dump/restore commands work against a real Postgres (runs in CI).

## Building the images

`control-plane-api`, `wallboard-worker`, and `simulator` build from
`infra/compose/docker/*.Dockerfile` with the **repo root** as build
context — this is a `uv` workspace, and each service depends on sibling
`packages/*` via workspace path dependencies, so the whole workspace has to
be in the build context. `.dockerignore` at the repo root keeps that context
small. These Dockerfiles are packaging only; no application logic lives in
them.

## Verifying without a NUC

This stack was authored in a sandbox with no Docker daemon and no NUC
hardware. What *was* validated there: both compose files parse as valid
YAML with the expected service graph; `caddy validate` accepts the
Caddyfile; the real `mosquitto.conf`/`acl.conf` were loaded into a local
Homebrew Mosquitto broker and driven with `mosquitto_pub`/`mosquitto_sub` to
confirm anonymous-refused and per-user topic scoping match what
`tests/integration/test_mosquitto_acl.py` asserts; and the Postgres
dump/restore commands ran against a real local Postgres
(`tests/integration/test_postgres_backup_restore.py`, passes locally too).

What was **not** run here and needs Docker (this sandbox has none — CI
does, via the Docker-gated tests above): `docker compose config`/`up`
against either file, an actual image build, Home Assistant entities
rendering in its UI, and a real NAS mount for `postgres-backup`. See the PR
description for the full list.
