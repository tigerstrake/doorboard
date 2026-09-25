# Boot and Recovery

**Status:** Verified in simulation; commands re-checked against the units 2026-09-25
**Walkthrough Date:** 2026-07-08 (Simulated/mock mode verified; hardware-specific steps marked and deferred)
**Verified-on:** simulator (mock mode), 2026-07-08 — not yet re-walked on the door Pi (home layout)

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
   Identify errors or boot loop traces. The journal is persistent on the SSD
   (`var-log-journal.mount`), so the *previous* boot is available with `-b -1` —
   that is where a crash-reboot's cause is:
   ```bash
   journalctl -u door-api -n 100 --no-pager
   journalctl -u door-visiond -n 100 --no-pager
   journalctl -u door-media -n 100 --no-pager
   journalctl -u door-sync -n 100 --no-pager
   journalctl -b -1 -p warning --no-pager      # everything at warning+ from the boot before this one
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
   - **door-voice:** `curl http://127.0.0.1:8084/health` (only when the greeting is enabled)
   - **door-proxy** (not on the door path): `curl -s -o /dev/null -w '%{http_code}\n' http://door-pi.local/session` → `404`
5. **Verify the kiosks:**
   On the live Pi the kiosks are launched by labwc's autostart
   (`deploy/pi-door/labwc-autostart.example`), not by systemd units:
   ```bash
   pgrep -af 'chromium.*--kiosk'          # one process per kiosk (doorpad, wallboard)
   loginctl list-sessions                 # the kiosk user's Wayland session must be active
   ```
   (With the canonical `chromium-kiosk@` units instead: `systemctl status chromium-kiosk@wallboard chromium-kiosk@doorpad`.)

---

## Step-by-Step Fix

### Scenario A: Kiosk UI Frozen or Blank Screen
If the physical buttons work and the ESP32 responds, but the screens are frozen:
1. Relaunch the kiosk. The labwc autostart loop restarts Chromium as soon as it exits,
   so killing it is the restart:
   ```bash
   pkill -f 'chromium.*kiosk-doorpad'      # or kiosk-wallboard; relaunches within ~3 s
   ```
   (Canonical units: `sudo systemctl restart chromium-kiosk@doorpad`.) If nothing comes
   back, the whole session: `loginctl terminate-user <kiosk-user>` and let auto-login
   start labwc again — the autostart waits on door-api `/health` before launching.
2. Confirm the outputs are detected (hardware-specific, labwc):
   ```bash
   loginctl list-sessions
   wlr-randr                                # from inside the session: both outputs listed?
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

### Scenario C: The Pi Reboots on Its Own
Nothing in this repo configures a hardware watchdog (`RuntimeWatchdogSec` is unset and
the `watchdog` package is not installed), so a spontaneous reboot is power, thermal, or
the kernel — not a watchdog timeout.
1. Read the boot before this one; the journal is persistent on the SSD:
   ```bash
   journalctl -b -1 -p err --no-pager | tail -50
   vcgencmd get_throttled                  # 0x0 = never throttled/under-volted since boot
   ```
   There is no `/var/log/syslog` on Bookworm; the journal is the log.
2. A unit that keeps failing is *not* parked any more (`StartLimitIntervalSec=0`), so a
   crash loop shows as `activating (auto-restart)` in `systemctl status`, with the
   reason in `journalctl -u <unit>`.

---

## Verification

1. **Verify Services Running:**
   ```bash
   systemctl is-active door-api door-visiond door-media door-sync door-proxy doorboard-enrollment-unlock.timer
   ```
   Output should be `active` for all (`door-voice` too if the greeting is enabled).
2. **Offline Kiosk Smoke Test:**
   Press the physical bell button. Verify that the ESP32 sounds the chime, the LED ring spins, and the DoorPad display transitions to "Visitor Mode" immediately.
3. **Check Metrics:**
   Verify that uptime counters increment:
   ```bash
   curl -s http://127.0.0.1:8080/metrics | grep uptime
   ```
