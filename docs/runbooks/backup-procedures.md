# Backup and Restore Procedures

**Status:** Verified
**Walkthrough Date:** 2026-07-08 (Simulated/mock mode verified; hardware-specific steps marked and deferred)

This document outlines the backup schedule, data scopes, retention rules, and step-by-step restoration procedures for all state-bearing components of the Doorboard system.

---

## 1. Summary of Backup Scopes

The system state is distributed across three zones with distinct privacy and persistence requirements:

| State Scope | Source Location | Backup Destination | Cadence | Retention | Privacy Restriction |
|---|---|---|---|---|---|
| **PostgreSQL Database** (Config, event log, social data) | NUC SSD volume | NAS Backups Share (`doorboard/backups/`) | Daily (Every 24h) | 14 days | None (Internal database) |
| **Media Archives** (Visitor video messages, photos) | Pi USB SSD (`/mnt/ssd/doorboard/`) | NAS Media Share (`doorboard/recordings/`) | Continuous (On session completion) | Lifetime (NAS governed) | High (Visitor visibility notice required) |
| **Biometric Store** (Face match database & embeddings) | Pi USB SSD (`/mnt/ssd/doorboard/visiond/`) | **None** (Locked to local SSD) | N/A | N/A | **Non-Negotiable:** Embeddings must never leave the physical door assembly (ADR-0008). |

---

## 2. Postgres Backup & Restore

### A. Backup Configuration
Postgres backups are managed by the containerized `postgres-backup` service on the NUC (see `infra/compose/docker-compose.yml`).
* It runs the script `infra/compose/backup/pg-backup.sh`.
* It calls `pg_dump --format=plain --no-owner --no-privileges | gzip -9` to dump the database.
* The dump and a matching `.sha256` hash are written to the host bind-mount of the NAS backups share (`NAS_BACKUP_PATH`).
* Backups older than 14 days (`BACKUP_RETAIN_COUNT`) are automatically pruned.

### B. Restore Validation Walkthrough
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
   gunzip -c "$NAS_BACKUP_PATH/doorboard-<timestamp>.sql.gz" \
     | psql -h localhost -U doorboard doorboard_restore_check
   ```
3. **Run Integrity Check:**
   Verify the event count matches the expected threshold:
   ```bash
   psql -h localhost -U doorboard doorboard_restore_check -c "SELECT count(*) FROM events;"
   ```
4. **Finalize Production Restore:**
   Once the scratch database is validated, swap it into production:
   ```bash
   # Terminate database connections and drop production DB
   dropdb -h localhost -U doorboard doorboard
   createdb -h localhost -U doorboard doorboard

   # Restore into production DB
   gunzip -c "$NAS_BACKUP_PATH/doorboard-<timestamp>.sql.gz" \
     | psql -h localhost -U doorboard doorboard

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
* All biometrics live exclusively on the hallway-facing USB SSD inside the locked physical enclosure.
* If the Pi 5 motherboard dies, the SSD is swapped to the replacement Pi. The database path `/mnt/ssd/doorboard/visiond/enrollment.sqlite` is instantly reread by `door-visiond` on boot.

### B. Recovery from SSD Hardware Failure
* If the physical USB SSD suffers a hardware failure, **all face matching profiles are lost**.
* For privacy protection, no backup copy exists.
* **Action:**
  1. Install a new USB SSD on the Pi (see [pi-replacement.md](pi-replacement.md)).
  2. Open the Enrollment UI (`http://door-pi.local:8080/admin/enrollment`).
  3. Re-enroll consenting users from scratch.
