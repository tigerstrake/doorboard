# T-501: control-plane-api — Postgres, ingestion, config distribution

**Agent:** codex · **Milestone:** M5 · **Depends on:** T-002
**Why this agent:** durable-data correctness (idempotent ingestion, migrations) and the trust-boundary-sensitive token/config machinery.

## Context

Spec: [apps/control-plane-api/README.md](../../apps/control-plane-api/README.md); trust model ARCHITECTURE.md §2. First real NUC service. May be developed fully against local Postgres + simulator.

## Deliverables

- FastAPI service + PostgreSQL schema (SQLAlchemy/Alembic): append-only event store (idempotent by `event_id`), sessions/media metadata mirrors, social-content durable store, presence history (broad labels only).
- `/ingest` endpoint: token-authenticated (packages/auth service tokens), batch event submission from door-sync, idempotency + per-batch results.
- Token management: issue/revoke Pi-scoped tokens (upload, ingest) — admin-auth CLI or endpoint; revocation takes effect immediately.
- Config distribution: versioned config bundle endpoint (`/config/door/{door_id}`) with checksum; bundle contents defined in packages/config; **no secrets beyond the Pi's own scope ever enter a bundle**.
- Social moderation/deletion endpoints backing the admin panel; `social.deletion_requested` honored against durable copies.
- Notifications: pluggable notifier interface + one concrete channel (HA notify or ntfy — choose, document) for: missed bell, storage alert, sync failing > N hours.
- MQTT audit fan-out (Mosquitto): publish ingested event summaries to `doorboard/#` topics for HA/monitoring consumption.

## Out of scope

HA bridge config/entities (T-503), presence computation (T-504), door-sync itself (T-502), dashboards.

## Acceptance criteria

- Replay the same batch 10× → identical DB state (idempotency proven).
- Alembic migrations from empty → head → downgrade one → up again, clean.
- Revoked token rejected on the very next request; config bundle for the Pi verified secret-free by a test that greps bundle contents against a denylist.
- Runs in compose (T-503 consumes); simulator-driven integration test covers ingest→store→notify.
