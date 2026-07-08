# Storage Full

**Walkthrough Date:** July 8, 2026 (Verified successfully)

## Symptoms

- Visitors are unable to leave video messages (recording fails to start or cuts off immediately).
- Media player/Streamer logs show `OSError: [Errno 28] No space left on device`.
- Storage critical notification alerts (`system.storage_alert` status is FIRING) are pushed to the owner's phone.
- Postgres fails to append new events, and database transactions are rolled back.

## Diagnosis

1. **Check Disk Space**: SSH into the Pi/NUC and run `df -h` to verify space on the SSD mounts:
   ```bash
   df -h /mnt/ssd/doorboard
   ```
2. **Find Disk Space Hogs**: Locate the largest directories on the mount:
   ```bash
   sudo du -sh /mnt/ssd/doorboard/* | sort -h
   ```
   Typically, `/mnt/ssd/doorboard/media/recordings/` (raw visitor video clips) is the primary user of disk space.
3. **Verify Automatic Retention**: Review the current retention configuration settings in `/etc/doorboard/door-media.env`:
   ```ini
   MEDIA_RETENTION_DAYS=14
   ```

## Step-by-Step Fix

### Step 1: Trigger Automatic Pruning Manually
Before deleting files by hand, invoke the media service's built-in cleanup script which cleanly updates the database and deletes matched files:
```bash
ssh owner@door-pi.local "cd ~/dev/doorboard && python -m door_media.cleanup --force"
```

### Step 2: Safe Manual Cleanup (Emergency)
If automatic pruning did not free enough space (e.g. because of a sudden spike in long recordings):
1. Navigate to the recordings folder:
   ```bash
   cd /mnt/ssd/doorboard/media/recordings/
   ```
2. Find and safely delete unfinalized or temp recordings older than 3 days:
   ```bash
   sudo find . -name "*.tmp" -mtime +3 -delete
   ```
3. Locate fully synced recordings that have already been uploaded to the NUC/NAS. You can identify them by matching their database sync status:
   ```bash
   # Select IDs of synced recordings
   sqlite3 /mnt/ssd/doorboard/media/media.db "SELECT path FROM recordings WHERE sync_status='synced' AND started_at_utc < date('now', '-7 days');" > /tmp/synced_files.txt
   
   # Safely delete these files
   while read -r file; do
     if [ -f "$file" ]; then
       sudo rm "$file"
       sqlite3 /mnt/ssd/doorboard/media/media.db "UPDATE recordings SET path=NULL WHERE path='$file';"
     fi
   done < /tmp/synced_files.txt
   ```

### Step 3: Configure More Aggressive Retention
To prevent the issue from recurring:
1. Decrease the retention days in `/etc/doorboard/door-media.env` (e.g. from 14 days to 7 days):
   ```ini
   MEDIA_RETENTION_DAYS=7
   ```
2. Restart the media service:
   ```bash
   sudo systemctl restart door-media
   ```

## Verification

1. Run `df -h` again and verify that the used disk space is now below **80%**.
2. Trigger a test recording from the simulator or kiosk, and confirm that the recording finalizes and uploads successfully with no disk errors.
3. Check the Prometheus metrics endpoint to verify `door_media_ssd_free_bytes` reports a healthy capacity.
