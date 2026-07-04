# T-502: door-sync upload engine

**Agent:** codex · **Milestone:** M5 · **Depends on:** T-201, T-501
**Why this agent:** durability engineering — the "never lose a clip, never delete unverified" property is unforgiving.

## Context

Spec: [apps/door-sync/README.md](../../apps/door-sync/README.md); ADR-0007 (deletion only after verification); trust model (limited credentials only).

## Deliverables

- Durable SQLite (WAL) queue on SSD: enqueue from `media.recording_finalized` + local event batches (sessions, social) for mirror-to-NUC; survives reboot/crash at any point (crash-consistency tests).
- Upload workers: NUC target (`/ingest` + media upload) and NAS target behind `NucTarget`/`NasTarget`/`MockTarget` adapters; exponential backoff with jitter, per-item attempt caps with dead-letter state (surfaced, never silently dropped).
- Integrity: sha256 verified on the far side (or verified read-back) before `sync.upload_completed`; only that event licenses door-media local deletion; idempotent re-upload by `(item_id, sha256)`.
- Queue visibility: `/metrics` (depth, oldest age, per-target status), `GET /queue` admin endpoint feeding the T-203 gauges.
- Outage endurance: NAS/NUC down for days → bounded local behavior coordinated with retention (oldest *synced* media reclaimed first; unsynced protected until dead-letter).
- Biometric fence: enrollment/embedding paths are structurally unreachable by the sync engine (allowlist of syncable roots; test proves embeddings dir excluded).

## Out of scope

What the NUC does with data (T-501), retention policy logic (door-media owns it; coordinate via events), NAS share provisioning (deploy/nas docs).

## Acceptance criteria

- Chaos test: random kill -9 during every phase (enqueue, mid-upload, pre-verify, post-verify) × 100 iterations → zero lost items, zero premature deletions, zero duplicates on the far side.
- Simulated 48 h NAS outage under continuous recording → queue grows bounded, drains completely on recovery, dead-letters only per policy.
- Pi-side config contains only the limited upload/ingest tokens (grep test vs denylist).
