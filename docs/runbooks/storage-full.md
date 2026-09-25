# Storage Full (SSD Pressure)

**Status:** Verified — commands re-checked against the source 2026-09-09
**Walkthrough Date:** 2026-07-08 (Simulated/mock mode verified; hardware-specific steps marked and deferred)
**Verified-on:** simulator (mock mode), 2026-07-08 — not yet re-walked on the door Pi (home layout)

> The SQL and log keys below were wrong from the first draft: they named a `recordings`
> schema (`id`, `filepath`, `synced`, `created_at`) and two log keys that have never existed
> in this codebase. Every query errored and both greps returned nothing — during a storage
> incident, which is the worst time to discover a runbook was never run. Re-verified against
> `door_media/db.py` and `door_media/service.py`; keep it that way.

## The Guarantee This Runbook Protects

The system must never write to the OS microSD card for media storage, and must stop recording safely before the USB SSD fills up completely. When free space drops below the threshold, recording shuts down gracefully to prevent data corruption, while core door interactions (button chimes and kiosk UIs) remain functional.

---

## Symptoms

- **Low Storage Alert:** The Prometheus alert `StorageLow` triggers (fires when free space is under 10 GiB).
- **Recording Stopped:** `door-media` emits `media.storage_status` with `recording_allowed: false`
  (every 30 s). That is the authoritative signal — there is no dedicated "recording disabled" log
  line. `door_media_ssd_free_bytes` on `/metrics` is the same fact as a gauge. A hard failure to
  even read the disk logs `disk_usage_failed`.
- **Sync Backlog:** `door-sync` metrics show high queue depth and media files are not uploading.
- **Disk pressure:** Command `df -h /mnt/ssd` shows usage at >95%.

---

## Diagnosis

1. **Check Disk Space:**
   On the Pi, query the disk usage of the SSD mount:
   ```bash
   df -h /mnt/ssd
   ```
2. **Identify Space Consumers:**
   Locate where the disk usage is accumulated:
   ```bash
   sudo du -sh /mnt/ssd/doorboard/*
   ```
   Typically, `/mnt/ssd/doorboard/recordings/` holds the bulk of the data.
3. **Check Media Service Metrics:**
   Query the `door-media` service for free space and queue depth:
   ```bash
   curl -s http://127.0.0.1:8082/metrics | grep -E "ssd_free|sync_queue_depth|oldest_unsynced"
   ```
   If `door_media_sync_queue_depth` is high (e.g. hundreds of clips), it means recordings are not being uploaded to the NAS and cannot be safely pruned.

---

## Step-by-Step Fix

### Scenario A: Large Backlog Due to NUC/NAS Outage
If the NAS was offline, files could not be uploaded and therefore could not be pruned.
1. Resolve the NUC/NAS connection first (see [network-outage.md](network-outage.md)).
2. Once the network is up, check that `door-sync` is draining the queue.
3. If space is critical (<4 GiB) and you cannot wait for the upload to complete, you can manually select and delete **non-critical** media (e.g., older photo booth images or standard visitor clips) that have NOT synced.
   - Run a query to locate unsynced files:
     ```bash
     sqlite3 /mnt/ssd/doorboard/door_media.db \
       "SELECT recording_id, kind, path, size_bytes FROM recordings
         WHERE sync_status = 'pending' AND path IS NOT NULL
         ORDER BY started_at_utc ASC LIMIT 20;"
     ```
   - `sync_status` is TEXT (`pending` / `synced` / `deleted`), not the `synced` integer an
     earlier draft of this runbook used. Rows keep a `deleted_at_utc` tombstone, so already-deleted
     recordings still appear in a naive `SELECT *` — filter on `sync_status` as above.
   - Delete selected files using the admin API so the database updates:
     ```bash
     curl -X DELETE -H "Authorization: Bearer <admin-token>" http://127.0.0.1:8082/recordings/<id>
     ```

### Scenario B: Synced Clips Not Being Cleaned Up
If the automated pruner has not run:
1. Trigger a manual prune cycle by lowering the retention limits temporarily in `/mnt/ssd/doorboard/.env`:
   ```ini
   # Lower bell clip retention from 3 days to 1 day (86400 seconds)
   DOOR_MEDIA_BELL_CLIP_MAX_AGE_S=86400
   ```
2. Restart the media service to force immediate retention enforcement:
   ```bash
   sudo systemctl restart door-media
   ```
3. Watch the logs to confirm deletions:
   ```bash
   journalctl -u door-media -f | grep -E "recording_deleted|segments_pruned|photo_review_pruned|retention_loop"
   ```

### Scenario C: Clean up Synced Files Manually
To safely clean up space manually without database inconsistency:
1. Locate files that are already successfully synced to the NAS:
   ```bash
   sqlite3 /mnt/ssd/doorboard/door_media.db \
     "SELECT recording_id, kind, path, size_bytes FROM recordings
       WHERE sync_status = 'synced' ORDER BY started_at_utc ASC;"
   ```
2. Delete them safely via the API:
   ```bash
   # Iterate over the synced IDs and call DELETE
   curl -X DELETE -H "Authorization: Bearer <admin-token>" http://127.0.0.1:8082/recordings/<recording_id>
   ```

---

## Verification

1. Verify that free space has increased:
   ```bash
   df -h /mnt/ssd
   ```
2. Check that the `/metrics` endpoint reports free space above the limit:
   ```bash
   curl -s http://127.0.0.1:8082/metrics | grep door_media_ssd_free_bytes
   ```
3. Verify that the media service has resumed recording:
   - Walk in front of the camera or ring the bell.
   - Confirm a new recording segment is created on the SSD.
   - Confirm the next `media.storage_status` reports `recording_allowed: true`.
