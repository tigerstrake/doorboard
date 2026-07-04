# door-sync — async Pi → NUC/NAS bridge

**Plane:** real-time door (but entirely off the critical path) · **Host:** door Pi 5 · **Language:** Python · **Task:** T-502

Drains local queues to the control plane and archive. This is the only component allowed to talk to the NAS from the Pi, using a limited-scope upload credential only (never NAS admin).

## Responsibilities

- Upload finalized clips/thumbnails/metadata and mirror local events (sessions, guestbook, polls) to the NUC.
- Durable queue in SQLite (WAL) on the SSD; survives reboots and outages of any length.
- Exponential backoff with jitter; per-item attempt tracking; `sync.upload_failed` events with `next_retry_at`.
- Deduplication: uploads idempotent by `(item_id, sha256)`; re-sending after a crash must not duplicate archive entries.
- Integrity: verify checksum on the far side (or verified read-back) **before** emitting `sync.upload_completed`; door-media may delete local copies only after that event.
- Queue visibility: depth, oldest-item age, and per-target status exposed via `/metrics` and consumed by the admin UI.

## Must never

Block or slow any visitor interaction; hold NAS/HA admin credentials; delete anything it hasn't verified; sync enrollment embeddings off the Pi (identity data stays local by default — only clip/metadata artifacts and non-biometric events sync).

## Interfaces

Events out: `sync.upload_queued/_completed/_failed`. HTTP: `/health`, `/metrics`, `GET /queue` (admin). Targets behind adapters: `NucTarget`, `NasTarget`, `MockTarget`.
