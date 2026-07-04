# control-plane-api — trusted control plane

**Plane:** control · **Host:** NUC · **Language:** Python · **Tasks:** T-501, T-504

The trusted aggregation point. Receives the door plane's event stream, owns durable history in PostgreSQL, bridges Home Assistant, computes presence status, hosts social-feature storage/moderation, distributes config to the Pi, and sends notifications.

## Responsibilities

- **Event ingestion:** authenticated endpoint for door-sync; events land in Postgres (append-only tables keyed by `event_id`, idempotent).
- **Presence/Weasley clock:** compute `status.presence_changed` with the fixed precedence — manual > focus shortcut > geofence label > calendar > default. Broad labels only; sources arrive via HA webhooks/phone shortcuts. Manual override always wins.
- **Home Assistant bridge:** consume/emit via Mosquitto and the HA API with a scoped token; HA automations never gate door behavior.
- **Social storage:** guestbook/poll/scoreboard/mood durable storage, moderation and deletion endpoints honoring `social.deletion_requested`.
- **Config distribution:** versioned config bundles the Pi pulls (never pushes secrets to the Pi beyond its limited scope).
- **Notifications:** owner pings (bell missed, storage alerts, sync failures) via HA/ntfy-style channel chosen in the brief.

## Must never

Sit in the door critical path; expose admin surfaces without auth; store raw biometrics (it never receives them by design); publish exact location or calendar contents to anything the wallboard can read.

## Interfaces

HTTP: `/health`, `/metrics`, `/ingest` (token-auth), `/status/*`, `/social/*`, `/config/door/{door_id}`. MQTT: `doorboard/#` topics for HA/audit fan-out. DB: PostgreSQL with Alembic migrations.
