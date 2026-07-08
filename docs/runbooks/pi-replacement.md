# Pi Replacement

**Walkthrough Date:** July 8, 2026 (Verified successfully)

## Symptoms

- Pi hardware is dead (no LEDs, burnt smell, or board fails to post).
- MicroSD card corruption (Green ACT LED blinks in a repetitive pattern, e.g., 4 long + 4 short flashes, indicating start.elf not found).
- Pi cannot be reached via SSH (`ssh: connect to host door-pi.local port 22: Connection refused`).
- Camera feed or ESP32 link reports disconnected continuously on the NUC wallboard.

## Diagnosis

1. **Activity LED Check**: Observe the Pi's Green ACT LED. If it blinks in an error code, the SD card is corrupted or missing required boot files.
2. **HDMI Console Output**: Connect a monitor/kiosk screen to the Pi's micro-HDMI port and watch the boot console. Look for `EXT4-fs error` or filesystem read failures.
3. **LAN DHCP Verification**: Log into the router/switch management interface and check if the Pi's MAC address is listed or has been assigned an IP address.

## Step-by-Step Fix

### Step 1: Flash a new microSD card
1. Obtain a replacement high-end microSD card (minimum 32GB Class 10/U3, preferably endurance/industrial grade).
2. Download the base Raspberry Pi OS Lite (64-bit) image.
3. Open **Raspberry Pi Imager** on your laptop:
   - Select OS: Raspberry Pi OS Lite (64-bit).
   - Select Storage: The replacement microSD card.
   - Click "Edit Settings" (Gear icon):
     - Set hostname to `door-pi.local`.
     - Enable SSH (use authorized keys).
     - Set username to `owner` and generate a secure password.
     - Save and flash.

### Step 2: Restore config bundle
The door Pi relies on specific configurations (like device tokens, SSL certs, and port mapping).
1. Copy the latest config bundle from the backups directory:
   ```bash
   tar -xzvf /mnt/nas-backups/doorboard-config-latest.tar.gz -C /tmp/pi-config
   ```
2. Mount the flashed microSD card on your laptop.
3. Copy the configuration files to the Pi's root filesystem partition:
   ```bash
   sudo cp -r /tmp/pi-config/etc/doorboard /volumes/rootfs/etc/
   sudo cp /tmp/pi-config/etc/wpa_supplicant/wpa_supplicant.conf /volumes/rootfs/etc/wpa_supplicant/
   ```
4. Unmount the microSD card, insert it into the new Pi, and power it up.

### Step 3: Re-enroll faces
> [!IMPORTANT]
> Biometric face embeddings do not restore from backups. Under Privacy Invariant §9, raw face images and embeddings are stored locally on the Pi's secure SSD and are never backed up or transmitted to the network. Replacing the Pi requires visitors and roommates to re-enroll.

1. Once the Pi is up and SSH is working, connect to it and run the project installation setup:
   ```bash
   ssh owner@door-pi.local
   cd ~/dev/doorboard && ./scripts/setup
   ```
2. Open the Admin UI console on the NUC or Wallboard.
3. Instruct roommates and visitors to access the enrollment panel to re-enroll:
   - Navigate to `/admin/enrollment` on the wallboard web UI.
   - Enter name/subject ID.
   - Follow the visual prompts to take 5 stable face snapshots.
   - Embed and save.

## Verification

1. Ping `door-pi.local` successfully.
2. Confirm the `door-api` and `door-visiond` services are running:
   ```bash
   sudo systemctl status door-api door-visiond
   ```
3. Check the camera stream liveness on the wallboard (Tile 3/4).
4. Perform a test face detection: stand in front of the camera and confirm the screen recognizes your name and updates the visitor session to VISITOR_MODE.
