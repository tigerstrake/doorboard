# Pi Replacement

**Status:** Verified
**Walkthrough Date:** 2026-07-08 (Simulated/mock mode verified; hardware-specific steps marked and deferred)

## Symptoms

- The Door Pi 5 is completely dead (no power, no green LED status, no response on UART/SSH/Ping).
- Multiple unrecoverable microSD/OS corruptions occur, requiring a clean OS re-install.
- Upgrading to new Pi hardware.

---

## Step-by-Step Replacement Procedure

### Step 1: Prepare the microSD OS Card
1. Download and open the **Raspberry Pi Imager** on a laptop.
2. Select **Raspberry Pi OS 64-bit Lite (Bookworm)**.
3. Under OS Customization:
   - Disable the default `pi` user. Create a dedicated administrative user (e.g., `doorboard-admin`).
   - Set up SSH keys (disable password authentication entirely for security).
   - Configure wireless network settings (SSID and passphrase) if not using Ethernet.
4. Flash the microSD card.

### Step 2: Enable Hardware Interfaces (Pi-specific, Hardware-only)
Before putting the card in the Pi, edit the `config.txt` file on the boot partition to enable UART (for the ESP32) and the camera:
```ini
# Enable UART for ESP32 transport
enable_uart=1
dtoverlay=uart0

# Enable CSI cameras
camera_auto_detect=1
```
Insert the microSD card into the new Pi 5.

### Step 3: Mount the USB SSD
All visitor video/audio messages, thumbnails, session databases, and face enrollment biometrics live on the **USB SSD**, not the microSD (per ADR-0007).
1. Plug the existing USB SSD into the Pi 5's USB 3.0 port.
2. Find the UUID of the SSD partition:
   ```bash
   sudo blkid | grep ext4
   ```
3. Create the mount directory:
   ```bash
   sudo mkdir -p /mnt/ssd
   ```
4. Edit `/etc/fstab` and add the mount configuration:
   ```ini
   UUID=<your-ssd-uuid> /mnt/ssd ext4 defaults,noatime,nofail 0 2
   ```
5. Mount the drive:
   ```bash
   sudo mount -a
   ```

### Step 4: Clone Codebase and Install Dependencies
1. Log in to the Pi 5 via SSH.
2. Clone the codebase and install workspace dependencies (Python 3.12, Node, PNPM, UV):
   ```bash
   git clone https://github.com/tigerstrake/doorboard.git /home/doorboard-admin/doorboard
   cd /home/doorboard-admin/doorboard
   # Set up python venv and install
   uv venv
   uv pip install -e "apps/door-api" -e "apps/door-visiond" -e "apps/door-media" -e "apps/door-sync"
   # Install node dependencies
   pnpm install
   ```
3. Copy systemd unit templates to the Pi systemd folder:
   ```bash
   sudo cp infra/systemd/*.service /etc/systemd/system/
   sudo systemctl daemon-reload
   ```

### Step 5: Restore Config Bundle from NUC
The Pi's app settings must be restored from the control plane NUC.
1. On the **NUC**, generate a new config-scoped token for the replacement Pi:
   ```bash
   uv run python -m control_plane_api.cli issue-token --door-id primary --scope config --label "replacement-pi"
   ```
2. Copy the output `token` value.
3. On the **Pi**, create the local `.env` file from `.env.example`:
   ```bash
   cp .env.example .env
   ```
4. Set the following variables in `.env`:
   - `DOORBOARD_ENV=pi-door`
   - `SYNC_INGEST_TOKEN=<the-token-copied-from-nuc>`
   - `CONTROL_PLANE_URL=http://<nuc-ip>:8090`
5. Restart the Pi services to let them sync down settings from the NUC:
   ```bash
   sudo systemctl enable door-api door-visiond door-media door-sync
   sudo systemctl start door-api door-visiond door-media door-sync
   ```

---

## Biometrics and Face Re-Enrollment Rules

> [!IMPORTANT]
> **Face biometrics and embeddings never leave the local SSD.** They are never backed up to the NUC or NAS for privacy reasons (ADR-0008).

Depending on whether the physical USB SSD was also replaced:

### Case A: Reused the old USB SSD
- **No re-enrollment needed.** The face match database lives in `/mnt/ssd/doorboard/visiond/enrollment.sqlite`.
- Once the mount is restored (Step 3) and `door-visiond` is restarted, recognition will function immediately with the existing profiles.

### Case B: Replaced the USB SSD with a new/blank SSD
- **Re-enrollment is required.**
- All previously enrolled users must be manually re-enrolled through the Enrollment UI (`http://<pi-ip>:8080/admin/enrollment` or via the visitor PWA flow).
- The new SSD will automatically create a fresh `/mnt/ssd/doorboard/visiond/enrollment.sqlite` on service start.

---

## Verification

1. Verify that all systemd units are healthy:
   ```bash
   systemctl status door-api door-visiond door-media door-sync
   ```
2. Verify local UI kiosk instances render correctly on HDMI-1 and HDMI-2.
3. Walk by the camera to verify that your cached profile is loaded and custom effects fire on button press.
