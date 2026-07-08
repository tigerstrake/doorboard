# Boot and Recovery

**Status:** Verified
**Walkthrough Date:** 2026-07-08 (Simulated/mock mode verified; hardware-specific steps marked and deferred)

## The Guarantee This Runbook Protects

Per [ARCHITECTURE.md](../../ARCHITECTURE.md) §10, the real-time door plane must boot and operate completely offline without internet or NUC connectivity. The critical path (`button → ESP32 feedback → local UI`) has zero dependencies on external services.

---

## Symptoms

- **Kiosk UI Stuck/Frozen:** The Wallboard (HDMI-1) or DoorPad (HDMI-2) shows a blank white screen, a Chromium crash page, or does not respond to touch.
- **Boot Loop / Watchdog Resets:** A service repeatedly crashes and restarts, or the ESP32 keeps executing its fallback/unavailable animation (blue wave/generic pulsing).
- **Service Failure:** Local health endpoints `/health` return errors or do not respond.
- **Cold Boot Failure:** After a physical power outage or hard reboot, one or more screens fail to render the UI.

---

## Diagnosis

1. **Check Service Status on Pi:**
   SSH into the door Pi (`ssh door-pi.local`) and list failed systemd services:
   ```bash
   systemctl list-units --type=service --state=failed
   ```
2. **Inspect Individual Service Logs:**
   Identify errors or boot loop traces:
   ```bash
   journalctl -u door-api -n 100 --no-pager
   journalctl -u door-visiond -n 100 --no-pager
   journalctl -u door-media -n 100 --no-pager
   journalctl -u door-sync -n 100 --no-pager
   ```
3. **Verify SSD Mount:**
   The active databases and recordings live on the USB SSD. Ensure it is mounted at `/mnt/ssd`:
   ```bash
   mount | grep /mnt/ssd
   df -h /mnt/ssd
   ```
   If `/mnt/ssd` is missing or mounted read-only (due to file system errors), check `dmesg | grep -i usb` and the `/etc/fstab` configuration.
4. **Test Local API Health Endpoints:**
   Each service exposes a local health check over HTTP loopback:
   - **door-api:** `curl http://127.0.0.1:8080/health` (default port 8080)
   - **door-visiond:** `curl http://127.0.0.1:8081/health`
   - **door-media:** `curl http://127.0.0.1:8082/health`
   - **door-sync:** `curl http://127.0.0.1:8083/health`
5. **Verify Kiosk Display Logs:**
   If display/touch is unresponsive:
   ```bash
   systemctl status chromium-kiosk@wallboard
   systemctl status chromium-kiosk@doorpad
   ```

---

## Step-by-Step Fix

### Scenario A: Kiosk UI Frozen or Blank Screen
If the physical buttons work and the ESP32 responds, but the screens are frozen:
1. Restart the kiosk service:
   ```bash
   sudo systemctl restart chromium-kiosk@wallboard
   sudo systemctl restart chromium-kiosk@doorpad
   ```
2. Confirm the display outputs are detected (Hardware-specific step, Wayland/Labwc dependent):
   ```bash
   loginctl-session  # get active sessions
   # View kiosk display manager status
   systemctl status display-manager
   ```

### Scenario B: Service Crash Loop / Database Corruption
If `door-api` or `door-media` boot-loops due to SQLite database corruption (e.g., after power loss):
1. Stop the affected service:
   ```bash
   sudo systemctl stop door-api
   ```
2. Integrity-check the SQLite DB file (e.g., `door-api` session DB):
   ```bash
   sqlite3 /mnt/ssd/doorboard/door-api/session.sqlite "PRAGMA integrity_check;"
   ```
3. If corruption is found, rename the corrupted database to keep it for debugging:
   ```bash
   mv /mnt/ssd/doorboard/door-api/session.sqlite /mnt/ssd/doorboard/door-api/session.sqlite.corrupt
   ```
4. Restart the service (it will automatically recreate a clean, empty database):
   ```bash
   sudo systemctl start door-api
   ```

### Scenario C: Watchdog Triggered Loop
If the hardware watchdog is rebooting the Pi continuously:
1. Connect via serial debug cable / UART (Hardware-specific step) or disable the watchdog temporarily:
   ```bash
   sudo systemctl stop watchdog
   ```
2. Diagnose the slow-startup service causing watchdog timeouts in `/var/log/syslog`.

---

## Verification

1. **Verify Services Running:**
   ```bash
   systemctl is-active door-api door-visiond door-media door-sync
   ```
   Output should be `active` for all.
2. **Offline Kiosk Smoke Test:**
   Press the physical bell button. Verify that the ESP32 sounds the chime, the LED ring spins, and the DoorPad display transitions to "Visitor Mode" immediately.
3. **Check Metrics:**
   Verify that uptime counters increment:
   ```bash
   curl -s http://127.0.0.1:8080/metrics | grep uptime
   ```
