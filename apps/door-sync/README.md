# door-sync — async Pi → NUC/NAS bridge

**Plane:** real-time door (but entirely off the critical path) · **Host:** door Pi 5 · **Language:** Python · **Task:** T-502

Drains local queues to the control plane and archive. This is the only component allowed to talk to the NAS from the Pi, using a limited-scope upload credential only (never NAS admin).

## Responsibilities

- Upload finalized clips/thumbnails/metadata and mirror local events (sessions, guestbook, polls) to the NUC.
- Maintain the private NAS gallery projection for approved photo-booth stills: album copies, manual tags, wallboard eligibility, and deletion across archive tiers.
- Durable queue in SQLite (WAL) on the SSD; survives reboots and outages of any length.
- Exponential backoff with jitter; per-item attempt tracking; `sync.upload_failed` events with `next_retry_at`.
- Deduplication: uploads idempotent by `(item_id, sha256)`; re-sending after a crash must not duplicate archive entries.
- Integrity: verify checksum on the far side (or verified read-back) **before** emitting `sync.upload_completed`; door-media may delete local copies only after that event.
- Queue visibility: depth, oldest-item age, and per-target status exposed via `/metrics` and consumed by the admin UI.

## Must never

Block or slow any visitor interaction; hold NAS/HA admin credentials; delete anything it hasn't verified; sync enrollment embeddings off the Pi (identity data stays local by default — only clip/metadata artifacts and non-biometric events sync).

## Interfaces

Events out: `sync.upload_queued/_completed/_failed`. HTTP: `/health`, `/metrics`, `GET /queue` (admin), internal gallery projection endpoints. Targets behind adapters: `NucTarget`, `NasTarget`, `MockTarget`, `GalleryStore`.

## Implementation (T-502)

**Durable queue.** SQLite `journal_mode=WAL`, `synchronous=FULL` at `${SSD_DATA_ROOT}/sync/queue.sqlite`. Every state transition is one committed transaction; all external side effects happen *before* the commit that records them, so a crash at any instant resumes from the last committed state. Item kinds: `clip`/`thumbnail` (→ NAS archive), `event` (→ NUC `/ingest` mirror), `purge` (→ NUC `DELETE /people/{id}/events`, ADR-0009). `item_id` is deterministic per logical item (clip = `recording_id`, event = `event_id`), so re-enqueue is idempotent and far-side dedupe holds.

**Deletion license (ADR-0007).** A clip is licensed for local deletion only after the archive target returns a checksum-verified copy: order is verify → mark `completed` → `POST /internal/sync_completed` to door-media → mark `licensed` → emit `sync.upload_completed`. door-media (which owns retention, T-203) performs the actual deletion. door-sync never deletes media itself.

**Enqueue sources.** Real-time via door-media's `GET /events` SSE (`media.recording_finalized`/`media.thumbnail_ready`); startup reconciliation via `GET /recordings?sync_status=pending` is the backstop for a missed event. Event mirror + purge arrive on loopback `POST /internal/enqueue` and `POST /internal/purge/{person_id}`.

**Retry policy.** Exponential backoff with jitter, bounded. *Transient* failures (target unreachable, 5xx, 401/403) retry forever within backoff and never dead-letter — a multi-day outage drains on recovery. Only *permanent* failures (4xx, checksum mismatch, missing local file) count toward `SYNC_MAX_PERMANENT_ATTEMPTS` and then dead-letter (surfaced via `/queue` + `/metrics`, never silently dropped). Completed items are pruned after `SYNC_COMPLETED_RETENTION_S`; dead-letters are kept.

**Biometric fence.** Media enqueue accepts a path only if it resolves inside an allowlisted root (`SYNC_SYNCABLE_ROOTS`, default `recordings,thumbnails`); the `visiond/` enrollment/embedding tree is excluded by omission and additionally denylisted, and `..`/symlink escapes are rejected. See `fence.py`.

**Private gallery.** `GalleryStore` has filesystem-NAS and mock implementations. Approval copies an already-saved `photo_booth` artifact into `gallery/albums/YYYY-MM/` with optional manual tags and a wallboard-moment flag. Wallboard moments are returned only when owner-approved and explicitly marked. `social.deletion_requested` with `target_kind="photo"` removes deterministic NAS base copies, gallery album copies, thumbnails, and consent metadata; door-api also asks door-media to remove the SSD copy.

**Config / env.** See `.env.example` (`--- door-sync ---`). The Pi holds only limited credentials: an ingest-scoped NUC token (`SYNC_INGEST_TOKEN`) and a limited NAS service target (`NAS_SYNC_TARGET`) — never Postgres/HA/NUC-admin/MQTT secrets (`tests/test_credential_fence.py` enforces this).

**Hardware-deferred.** The NAS adapter is filesystem-backed (models a mounted share exactly; CI/dev path). Real SFTP/rsync NAS provisioning + the systemd unit belong to deploy/nas scope. control-plane-api exposes no media-file upload endpoint, so media files archive to the NAS and only their metadata/events mirror to the NUC (matches ARCHITECTURE.md §6).
