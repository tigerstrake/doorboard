# Backup and Restore Procedures

**Status:** Verified in simulation; statements re-checked against the source 2026-09-25
**Walkthrough Date:** 2026-07-08 (Simulated/mock mode verified; hardware-specific steps marked and deferred)
**Verified-on:** simulator + a local Postgres, 2026-07-08 — the NAS mount and the LUKS store not yet re-walked on the devices

This document outlines the backup schedule, data scopes, retention rules, and step-by-step restoration procedures for all state-bearing components of the Doorboard system.

---

## 1. Summary of Backup Scopes

The system state is distributed across three zones with distinct privacy and persistence requirements:

| State Scope | Source Location | Backup Destination | Cadence | Retention | Privacy Restriction |
|---|---|---|---|---|---|
| **PostgreSQL Database** (Config, event log, social data) | NUC SSD volume | NAS Backups Share (`doorboard/backups/`) | Daily (Every 24h) | 14 days | None (Internal database) |
| **Media Archives** (Visitor video messages, photos) | Pi USB SSD (`/mnt/ssd/doorboard/`) | NAS Media Share (`doorboard/recordings/`) | Continuous (On session completion) | Lifetime (NAS governed) | High (Visitor visibility notice required) |
| **Biometric Store** (Face match database & embeddings) | Pi, LUKS volume on the SSD: `/mnt/vision-enrollment/doorboard/enrollment.sqlite` (container `/mnt/ssd/doorboard/enrollment.luks`; passphrase only on the NUC, ADR-0009 §6) | **None** | N/A | N/A | **Non-Negotiable:** Embeddings must never leave the physical door assembly (ADR-0009). |

---

## 2. Postgres Backup & Restore

### A. Backup Configuration
Postgres backups are managed by the containerized `postgres-backup` service on the NUC (see `infra/compose/docker-compose.yml`).
* It runs the script `infra/compose/backup/pg-backup.sh`.
* It runs `pg_dump --format=plain --no-owner --no-privileges` to a raw file first — deliberately **not** a `| gzip` pipeline, whose exit status would be gzip's — requires the raw dump to be at least 4 KiB, then compresses (`gzip -9`) and `gzip -t`s it before moving it into place.
* The dump and a matching `.sha256` hash are written to the host bind-mount of the NAS backups share (`NAS_BACKUP_PATH`), only if the share's `.doorboard-nas` marker is present (an unmounted share would otherwise silently receive dumps on the NUC's own disk).
* Backups beyond the newest 14 (`BACKUP_RETAIN_COUNT`) are pruned, and only after a successful dump.
* A failing job is visible: the compose healthcheck on `postgres-backup` turns `unhealthy` when no dump is younger than two `BACKUP_INTERVAL_S` (`docker compose ps`).

### B. Restore Validation Walkthrough

> **`-v ON_ERROR_STOP=1` is not optional.** By default `psql` reports a failed
> statement, applies everything after it, and still exits `0` — so a restore that
> rebuilt half the schema is indistinguishable from a clean one. `set -o pipefail`
> matters for the same reason: without it a pipeline reports only `psql`'s status,
> so a truncated or corrupt `gunzip` is invisible.

This procedure must be executed to verify the integrity of the dump before replacing a production database.

1. **Stop writing services:**
   Stop the control plane API on the NUC so no new events are ingested:
   ```bash
   docker compose -f infra/compose/docker-compose.yml stop control-plane-api
   ```
2. **Restore to a Scratch Database First:**
   Never restore directly over a live production database.
   ```bash
   # Create a test database
   createdb -h localhost -U doorboard doorboard_restore_check

   # Decompress and restore the latest dump from the NAS
   set -o pipefail
   gunzip -c "$NAS_BACKUP_PATH/doorboard-<timestamp>.sql.gz" \
     | psql -v ON_ERROR_STOP=1 -h localhost -U doorboard doorboard_restore_check
   ```
3. **Run Integrity Check:**
   Verify the event count matches the expected threshold:
   ```bash
   psql -v ON_ERROR_STOP=1 -h localhost -U doorboard doorboard_restore_check -c "SELECT count(*) FROM events;"
   ```
4. **Finalize Production Restore:**
   Once the scratch database is validated, swap it into production:
   ```bash
   # Terminate database connections and drop production DB
   dropdb -h localhost -U doorboard doorboard
   createdb -h localhost -U doorboard doorboard

   # Restore into production DB
   set -o pipefail
   gunzip -c "$NAS_BACKUP_PATH/doorboard-<timestamp>.sql.gz" \
     | psql -v ON_ERROR_STOP=1 -h localhost -U doorboard doorboard

   # Restart services
   docker compose -f infra/compose/docker-compose.yml up -d
   ```
5. **Clean up:**
   Drop the scratch verification database:
   ```bash
   dropdb -h localhost -U doorboard doorboard_restore_check
   ```

---

## 3. Media Archives Sync (Pi → NAS)

### A. Continuous Sync Architecture
* `door-media` records segments locally to `/mnt/ssd/doorboard/recordings/` on the Pi.
* Upon visitor session completion, the recording is finalized, and a file sync task is sent to the local `door-sync` SQLite-backed queue.
* `door-sync` uploads the media and matching thumbnails to the NAS (`NAS_SYNC_TARGET`).
* **Retention Rule (ADR-0007):** The local media segment is only deleted from the Pi's USB SSD *after* `door-sync` successfully validates the file size and checksum on the NAS destination.

### B. Restoration of Media
* In the event of a Pi hardware or SSD replacement, media archives do **not** need to be restored back to the Pi's USB SSD. The local SSD is designed for active caching and immediate session playback only.
* The historical media files remain accessible on the NAS share mounting point or via the Home Assistant media dashboard.

---

## 4. Biometric Store (Face Match Database)

> [!WARNING]
> **Privacy Invariant:** Under no circumstances are face embeddings, face signatures, or the enrollment database (`enrollment.sqlite`) permitted to be backed up to the NUC, the NAS, or any remote/cloud server.

### A. Local Resilience
* All biometrics live exclusively on the hallway-facing USB SSD inside the locked physical enclosure, inside a LUKS2 container whose passphrase is held only on the NUC (`CONTROL_PLANE_ENROLLMENT_KEY`).
* If the Pi 5 motherboard dies, the SSD is swapped to the replacement Pi — but the volume is ciphertext until that Pi holds the release token (`/etc/doorboard/enrollment-key.token`) and can reach the NUC's `GET /status/keys/enrollment`. Set both up ([pi-replacement.md](pi-replacement.md) Step 6) and `door-visiond` finds the same `enrollment.sqlite`; without the NUC the door runs `enrollment_locked` (recognition off, everything else fine).

### B. Recovery from SSD Hardware Failure
* If the physical USB SSD suffers a hardware failure, **all face matching profiles are lost**.
* For privacy protection, no backup copy exists.
* **Action:**
  1. Install a new USB SSD on the Pi (see [pi-replacement.md](pi-replacement.md)).
  2. Build a fresh volume (`scripts/create-enrollment-volume`, [enrollment-encrypted-volume.md](enrollment-encrypted-volume.md)).
  3. Re-enroll consenting users from scratch: the admin panel at `http://127.0.0.1:5173/admin` on the Pi, the DoorPad's own *Enroll my face*, or `scripts/enrollment-qr "Name"` for the phone path. (There is no `/admin/enrollment` route on door-api.)
