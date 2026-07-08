# Network Outage

**Walkthrough Date:** July 8, 2026 (Verified successfully)

## Symptoms

- Kiosk interface shows stale room/mood states and scoreboard details.
- Roommate check-ins or visitor bell rings do not publish notifications to the owner's mobile phone.
- `door-sync` service logs on the Pi show connection timeouts (`httpx.ConnectError` or `ConnectTimeout`) trying to reach the NUC.
- Prometheus shows the door Pi targets (e.g. `door-api`, `door-visiond`) as red/down.

## Diagnosis

1. **Verify Interface Link**: SSH into the Pi (locally via direct monitor/keyboard if LAN is completely down) and check interface states:
   ```bash
   ip link show eth0
   ip addr show eth0
   ```
2. **Ping the NUC Gateways**: Check connectivity to the local router and NUC:
   ```bash
   ping -c 3 192.168.1.1       # Router
   ping -c 3 NUC-IP-HERE      # NUC Control Plane
   ```
3. **Inspect the Sync Backlog**: Query the SQLite sync queue database on the Pi to see if events are buffering:
   ```bash
   sqlite3 /mnt/ssd/doorboard/sync/sync_queue.db "SELECT count(*), status FROM queue_item GROUP BY status;"
   ```

## Degraded Mode Expectations

Under the two-plane architectural design (ARCHITECTURE.md §1 & §10):
- **What keeps working**: The door plane (ESP32 bell button, camera pipeline, video recorders, and visitor session state machine) has **zero runtime network dependencies**. A visitor can still ring the bell, get immediate audio/LED feedback, see the screen transition, and record a video message.
- **What queues**: All events (bell rings, recording finalized events, etc.) and raw video files are saved to the Pi's local SSD and queued in the SQLite `sync_queue.db`. They will queue indefinitely (with exponential backoff retries) until connectivity to the NUC is restored.
- **What is lost temporarily**: Live wallboard web panels, real-time scoreboard modifications, and owner notifications.

## Step-by-Step Fix

### Step 1: Resolve LAN / Switch Connectivity
1. Verify ethernet cables are securely plugged into the Pi, NUC, and router/switch.
2. Check the port LEDs on the network switch.
3. Restart the Pi network service:
   ```bash
   sudo systemctl restart systemd-networkd
   ```

### Step 2: Renew DHCP Lease
If the Pi lost its IP address:
```bash
sudo dhclient -r eth0 && sudo dhclient eth0
```

### Step 3: Verify NUC Service Liveness
If the network is fine but the sync continues to fail, check if the NUC control plane API is down:
1. Log into the NUC:
   ```bash
   ssh owner@nuc.local
   ```
2. Check if the container is running and healthy:
   ```bash
   docker ps | grep control-plane-api
   ```
3. If it's down, follow [nuc-outage.md](nuc-outage.md).

## Verification

1. Ping to `control-plane-api` succeeds.
2. Monitor the `door-sync` log to verify that the queue is draining:
   ```bash
   sudo journalctl -u door-sync -f
   # Look for: "Successfully uploaded backlog item"
   ```
3. Confirm the SQLite sync queue depth is back to `0`:
   ```bash
   sqlite3 /mnt/ssd/doorboard/sync/sync_queue.db "SELECT count(*) FROM queue_item WHERE status='pending';"
   ```
