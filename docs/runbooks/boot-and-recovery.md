# Boot and Recovery

**Walkthrough Date:** July 8, 2026 (Verified successfully)

## Symptoms

- Kiosk display is completely blank (black screen or backlight off).
- Kiosk UI is frozen on a stale state (e.g. "Welcome" screen unresponsive to touches).
- Boot loop: Device repeatedly shows the BIOS splash screen and reboots.
- Services fail to start on boot (systemd reports failing Docker service or watchdog timeout).
- SSH connection to NUC or Pi is refused.

## Diagnosis

1. **Physical Power Check**: Inspect the NUC/Pi power LED. If it's red or off, the device is shut down.
2. **Ping Check**: Ping the device from another machine on the LAN:
   ```bash
   ping doorboard.local
   ```
3. **Kiosk Kiosk/Browser State**: If the device responds to ping and SSH but the kiosk screen is black/frozen, check the local X11/Wayland display server and browser processes:
   ```bash
   DISPLAY=:0 xdotool getactivewindow || export DISPLAY=:0
   systemctl status kiosk-browser
   ```
4. **Watchdog Log Inspection**: Inspect the system watchdog service logs to determine if it triggered a reset loop:
   ```bash
   journalctl -u watchdog -n 100 --no-pager
   ```
5. **Docker Health Check**: Check if the Docker daemon failed to start or is hung:
   ```bash
   systemctl status docker
   journalctl -u docker -n 50
   ```

## Step-by-Step Fix

### Scenario A: Unresponsive Kiosk UI (Soft Recovery)
1. Restart the kiosk display manager or kiosk-browser service:
   ```bash
   ssh owner@doorboard.local "sudo systemctl restart kiosk-browser"
   ```
2. If X11/Wayland is locked up, restart the display manager:
   ```bash
   ssh owner@doorboard.local "sudo systemctl restart lightdm" # or gdm
   ```

### Scenario B: Boot Loop / Watchdog Triggered (Hard Recovery)
1. **Cold Power Cycle**:
   - Pull the physical power plug from the NUC or Pi.
   - Wait exactly **10 seconds** to allow all capacitors on the board to fully discharge.
   - Plug the power back in.
2. **Bios/UEFI Selection (If stuck on boot splash)**:
   - Connect a USB keyboard directly to the NUC/Pi.
   - Power on the system and tap `F8` or `F12` to enter the boot menu.
   - Select the primary SSD/NVMe boot device (labeled `GRUB` or `Ubuntu`).
3. **Docker Daemon Recovery**:
   - If Docker is hung due to corrupted storage layers or deadlocked networks:
     ```bash
     sudo systemctl stop docker
     sudo rm -rf /var/lib/docker/network/files/local-kv.db
     sudo systemctl start docker
     docker system prune -af --volumes
     ```

### Scenario C: Offline Boot (Network unreachable on startup)
If the NUC boots without a working LAN connection, services may wait indefinitely for network resolution.
1. Force network-online timeout to bypass blockers:
   ```bash
   sudo systemctl stop systemd-networkd-wait-online
   ```
2. Manually bind fallback static link:
   ```bash
   sudo ip addr add 192.168.1.100/24 dev eth0
   ```

## Verification

1. Ping response is stable (0% packet loss).
2. Kiosk screen displays the main wallboard/doorpad interface correctly.
3. Verify all running Docker containers report healthy:
   ```bash
   docker compose -f infra/compose/docker-compose.yml ps
   ```
   All services must show `(healthy)` or `running`.
