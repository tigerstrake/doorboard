# Storage Full (SSD Pressure)

**Status:** Verified
**Walkthrough Date:** 2026-07-08 (Simulated/mock mode verified; hardware-specific steps marked and deferred)

## The Guarantee This Runbook Protects

The system must never write to the OS microSD card for media storage, and must stop recording safely before the USB SSD fills up completely. When free space drops below the threshold, recording shuts down gracefully to prevent data corruption, while core door interactions (button chimes and kiosk UIs) remain functional.

---

## Symptoms

- **Low Storage Alert:** The Prometheus alert `StorageLow` triggers (fires when free space is under 10 GiB).
- **Recording Stopped:** `door-media` logs show `storage_low_disable_recording` or `disk_space_exhausted`.
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
     sqlite3 /mnt/ssd/doorboard/door_media.db "SELECT id, filepath FROM recordings WHERE synced = 0 ORDER BY created_at ASC LIMIT 20;"
     ```
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
   journalctl -u door-media -f | grep prune
   ```

### Scenario C: Clean up Synced Files Manually
To safely clean up space manually without database inconsistency:
1. Locate files that are already successfully synced to the NAS:
   ```bash
   sqlite3 /mnt/ssd/doorboard/door_media.db "SELECT id, filepath FROM recordings WHERE synced = 1;"
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
   - Check that `door-media` logs show successful segment closure and queue enqueue.
