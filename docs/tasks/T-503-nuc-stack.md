# T-503: NUC compose stack — Mosquitto, Home Assistant bridge, Caddy

**Agent:** gemini · **Milestone:** M5 · **Depends on:** T-501
**Why this agent:** configuration-heavy assembly work with explicit specs.

## Context

Specs: [deploy/nuc/README.md](../../deploy/nuc/README.md), [infra/compose/README.md](../../infra/compose/README.md), [integrations/home-assistant/README.md](../../integrations/home-assistant/README.md), security §16 of the handoff.

## Deliverables

- `infra/compose/`: production compose for the NUC (Postgres with volumes+healthchecks, Mosquitto with per-device auth file, control-plane-api, wallboard-worker, HA, optional Caddy) + `compose.dev.yml` running the full simulated system on a laptop.
- Mosquitto: authenticated (no anonymous), per-device credentials, LAN-only exposure; topic ACLs limiting the Pi's credential to its own topics.
- HA bridge per integrations/home-assistant: MQTT discovery entities (bell event, presence, storage alert, sync status), inbound webhook config for Focus shortcuts/geofence labels → control-plane-api, scoped HA token setup documented.
- Caddy config (optional internal HTTPS) fronting control-plane-api/HA; nothing exposed beyond LAN.
- Postgres backup job: scheduled `pg_dump` to NAS path + restore procedure draft for the runbook.
- `.env.example` NUC section verified complete; secrets handling documented (env files, never in git or images).

## Out of scope

Service code changes (escalate), presence logic (T-504), monitoring stack (T-703), NAS share creation (owner/manual per deploy/nas).

## Acceptance criteria

- `docker compose up` on a clean machine (documented prerequisites) → all healthchecks green; `compose.dev.yml` runs the simulator stack on a laptop.
- Anonymous MQTT connection refused; Pi credential cannot publish outside its ACL (tested).
- Bell press in the simulated stack → HA entity updates + a test notification fires end-to-end.
- Backup job produces a dump that restores cleanly (demonstrated in CI or documented manual test).
