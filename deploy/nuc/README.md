# deploy/nuc — control plane deployment

Docker Compose stack (files in `infra/compose/`): Home Assistant, Mosquitto, PostgreSQL, control-plane-api, wallboard-worker, Caddy (if internal HTTPS), optional monitoring. Task: T-503 authors the compose files; T-501 the services.

- All admin secrets live here (`.env`, never in git). The NUC issues and can revoke the Pi's limited tokens.
- Postgres: volumes on NUC disk (never the NAS), scheduled `pg_dump` backups shipped *to* the NAS.
- Mosquitto: authenticated (per-device credentials), not exposed beyond the LAN/VLAN.
- Caddy fronts control-plane-api and HA if HTTPS-internal is adopted; nothing is exposed to the public internet (handoff §16).
- Update procedure and outage behavior documented in `docs/runbooks/` (door keeps working during NUC maintenance by design — verify with simulator scenario before relying on it).
