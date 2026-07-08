# Network Outage

**Status:** Verified
**Walkthrough Date:** 2026-07-08 (Simulated/mock mode verified; hardware-specific steps marked and deferred)

## The Guarantee This Runbook Protects

Per [ARCHITECTURE.md](../../ARCHITECTURE.md) §10, the system must support full offline operation. If the local network, VLAN routing, internet link, or control plane NUC goes offline:
- The physical bell button, ESP32 feedback, and local kiosk UIs must function normally.
- Video messages are recorded locally to the Pi's USB SSD.
- Events and media uploads queue up in the Pi's local SQLite queue via `door-sync` and are never lost.

---

## Symptoms

- **Alerts Firing:** `ServiceDown` for `control-plane-api` or `SyncQueueAging` triggers.
- **Kiosk Status:** The kiosk display shows warning overlays indicating "Control Plane Unreachable" or stale presence data.
- **No notifications:** Bell press does not trigger Home Assistant events or push notifications (via `ntfy`) on the owner's device.
- **Growing Sync backlog:** `door_sync_queue_depth` increments continuously.

---

## Diagnosis

1. **Test Pi ↔ NUC Connectivity:**
   From the Pi, ping the NUC IP or hostname:
   ```bash
   ping -c 4 nuc.local
   ```
2. **Check NUC API health:**
   Query the control plane API health endpoint from the Pi:
   ```bash
   curl -I http://<nuc-ip>:8090/health
   ```
3. **Verify VLAN Routing (Hardware-specific):**
   If ping fails, verify that the managed network switch ports for the Pi and NUC are on the correct VLANs and that routing between the secure control plane network and the hallway door plane network is active.
4. **Check Sync Queue Metrics:**
   Query the `door-sync` metrics to check queue growth:
   ```bash
   curl -s http://127.0.0.1:8083/metrics | grep door_sync_queue_depth
   ```
   If this number grows but does not decrease, a network link is down.

---

## Step-by-Step Fix

### Step 1: Inspect Physical and Interface Link State
1. Check the physical Ethernet port LEDs on the Pi and NUC.
2. Check local interface status:
   ```bash
   ip a
   ip link show eth0
   ```
3. If no IP is assigned, check the DHCP server status on the router/switch.

### Step 2: Resolve NUC Service Status
If the network is up but the NUC API is down, follow [nuc-outage.md](nuc-outage.md) to bring the control plane stack back up.

### Step 3: Monitor Queue Drain (Post-Restore)
Once connectivity is restored between the Pi and the NUC:
1. `door-sync` will automatically resume connection and begin draining the SQLite queue.
2. Monitor the drain process on the Pi:
   ```bash
   watch "curl -s http://127.0.0.1:8083/metrics | grep door_sync_queue_depth"
   ```
3. Confirm that the oldest pending item age decreases:
   ```bash
   curl -s http://127.0.0.1:8083/metrics | grep door_sync_oldest_pending_s
   ```
4. Verify on the NUC event log that new visitor sessions and events have durably stored.

---

## Verification

1. Verify that the NUC is reachable:
   ```bash
   ping nuc.local
   ```
2. Verify that the sync queue depth returns to `0` or near-zero baseline.
3. Check the Home Assistant dash to confirm the door sensors and camera feeds have transitioned from "Unavailable" to live/active.
