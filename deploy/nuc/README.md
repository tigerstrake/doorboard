# deploy/nuc — control plane deployment

Docker Compose stack (files in `infra/compose/`): Home Assistant, Mosquitto, PostgreSQL, control-plane-api, wallboard-worker, Caddy (if internal HTTPS), optional monitoring. Task: T-503 authors the compose files; T-501 the services.

- All admin secrets live here (`.env`, never in git). The NUC issues and can revoke the Pi's limited tokens.
- Postgres: volumes on NUC disk (never the NAS), scheduled `pg_dump` backups shipped *to* the NAS.
- Mosquitto: authenticated (per-device credentials), not exposed beyond the LAN/VLAN.
- Caddy fronts control-plane-api and HA if HTTPS-internal is adopted; nothing is exposed to the public internet (handoff §16).
- Update procedure and outage behavior documented in `docs/runbooks/` (door keeps working during NUC maintenance by design — verify with simulator scenario before relying on it).

## T-503 status (this task)

The compose stack, Mosquitto auth/ACLs, HA bridge config, optional Caddy
config, and the Postgres backup job are implemented in
[infra/compose/](../../infra/compose/README.md); the full walkthrough
(prerequisites, running it, per-service detail) lives there rather than
being duplicated here. This file stays the short "what lives on the NUC and
why" overview the handoff's trust model expects a reader to find first.

## Setting up a NUC from scratch

1. Install Docker Engine + Docker Compose v2 on the NUC's OS of choice
   (any Linux distro with Docker support; the handoff doesn't mandate one).
2. Clone this repo (or just `infra/compose/`, `integrations/home-assistant/`,
   and the root `.env.example` if you don't want the whole monorepo on the
   NUC — the compose files' `build.context` assumes the monorepo layout
   either way).
3. Mount the NAS backups share on the host at a stable path and point
   `NAS_BACKUP_PATH` in `.env` at it (share creation itself: `deploy/nas/`,
   owner/manual).
4. Copy the root `.env.example` to `.env`; fill in every NUC variable with
   freshly generated random values — **never** reuse the placeholder text,
   and never commit this file (enforced by `.gitignore` and a CI check).
5. `docker compose -f infra/compose/docker-compose.yml --env-file .env up -d`;
   confirm `docker compose ... ps` shows every service healthy.
6. Issue the door Pi's first `ingest` token:
   `docker compose ... exec control-plane-api control-plane-api-admin issue-token --door-id primary --scope ingest`
   — copy the printed `token=...` value into the Pi's own `.env` as
   `SYNC_UPLOAD_TOKEN` (out of git, lives on the Pi only; see the root
   `.env.example`'s Door Pi section). Repeat with `--scope config` if
   door-api needs to pull config bundles directly.
7. If adopting internal HTTPS: set `DOORBOARD_INTERNAL_DOMAIN` in `.env`,
   start with `--profile https`, and trust Caddy's internal CA on client
   devices (`infra/caddy/README.md`).

## Security checklist touchpoints (handoff §16)

- No anonymous MQTT; per-device Mosquitto credentials
  (`infra/compose/mosquitto/`).
- No secrets in git — `.env` only, `.gitignore`-enforced, checked for in CI.
- Nothing here binds to a public interface or gets port-forwarded; VLAN
  segmentation is a router/host-network concern this stack assumes, not
  something Compose can enforce on its own (see `infra/compose/README.md`).
- Tokens are revocable immediately (`DELETE /admin/tokens/{token_id}` or
  `control-plane-api-admin revoke-token`, T-501) — rotate the door Pi's
  token if it's ever physically lost (ARCHITECTURE.md §2: a stolen Pi holds
  no admin credentials and no long-term archive).

The full deployment security checklist itself is T-701's
(`docs/runbooks/security-checklist.md`, not yet written).
