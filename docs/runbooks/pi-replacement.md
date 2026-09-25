# Pi Replacement

**Status:** Draft — re-walk on hardware. Rewritten 2026-09-25 around the home layout the live door actually runs (`deploy/pi-door/install-home-layout`), the encrypted enrollment volume (ADR-0009 §6) and the LAN proxy (ADR-0047). The previous version built a Pi the installer refuses, skipped the LUKS volume, enabled a GPIO UART the ESP32 does not use, and pointed at an enrollment URL that does not exist.
**Verified-on:** not yet — every step below was checked against the repo, none against a fresh Pi. Walk it once on hardware and record the date here.

## Symptoms

- The door Pi 5 is dead (no power LED activity, no response on SSH/ping, no display).
- The microSD/OS is unrecoverably corrupt and needs a clean re-install.
- Upgrading to new Pi hardware.

## What you need before starting

| Item | Where it comes from |
|---|---|
| The values from the old Pi's `~/doorboard/.env` | Your own copy. It holds only Pi-scoped secrets; if it is gone with the Pi, mint new ones: `SYNC_INGEST_TOKEN` on the NUC, fresh `DOOR_*_ADMIN_TOKEN`s, and re-enter the pairs the NUC/relay hold (`CONTROL_PLANE_DOOR_API_ADMIN_TOKEN`, the two relay device tokens). See [token-rotation.md](token-rotation.md) — a lost Pi is a stolen Pi for this purpose. |
| `CONTROL_PLANE_ENROLLMENT_KEY_TOKEN` | NUC `.env`. Goes into `/etc/doorboard/enrollment-key.token` on the Pi (root, 0600). Never the passphrase itself. |
| The NUC reachable at `http://<nuc>:8090` | The LUKS passphrase is released from there at every boot; without it recognition stays off (the door still works). |
| The NAS upload credential | For `/mnt/nas/doorboard`; the limited service account, never the NAS admin. |

## Step 1 — Flash the microSD

1. Raspberry Pi Imager → **Raspberry Pi OS 64-bit (Bookworm), Desktop** (the kiosks run on labwc/Wayland, which the Desktop image ships; Lite needs the session installed by hand).
2. OS customisation: hostname **`door-pi`** (everything on the LAN and the NUC reaches the door as `door-pi.local`), a login user (the live Pi uses `tigerstrake`; the run user is whatever you choose here), SSH with **keys only**, wifi or wired as you use it.
3. Nothing in `config.txt` for the ESP32: the controller is on USB CDC (`/dev/ttyACM1`, `ESP32_UART_DEVICE`), and the GPIO UART header is taken by the AI HAT+. `camera_auto_detect=1` is the default. Do **not** add `enable_uart=1` / `dtoverlay=uart0`.

Boot it, log in, `sudo apt update && sudo apt full-upgrade`.

## Step 2 — Packages

```bash
sudo apt install git cryptsetup caddy chromium rpicam-apps hailo-all \
                 prometheus-node-exporter   # optional: NUC thermal panel
sudo systemctl disable --now caddy prometheus-node-exporter   # the installer does this too
```

`hailo-all` is the Raspberry Pi HailoRT 4.x stack (`apps/door-visiond` is validated on 4.23); confirm `hailortcli fw-control identify` sees the HAT. Add the run user to `dialout video render audio`. Install `uv`, Node 22 and pnpm (Corepack) as on any dev machine.

## Step 3 — Storage

1. SSD (reused or new) into a USB 3 port. `sudo blkid` for the ext4 partition's UUID, then in `/etc/fstab`:
   ```
   UUID=<ssd-uuid>  /mnt/ssd  ext4  defaults,noatime,nofail  0 2
   ```
   `sudo mkdir -p /mnt/ssd && sudo mount -a`. `doorboard-storage-ready.service` refuses to start the door services if `/mnt/ssd/doorboard` ends up on the root filesystem, so a missing SSD is loud, not silent.
2. NAS share at `/mnt/nas/doorboard` (fstab CIFS/NFS entry with a credentials file for the upload account). door-sync tolerates it being absent — clips wait on the SSD.
3. `sudo mkdir -p /mnt/ssd/doorboard && sudo chown <run-user>: /mnt/ssd/doorboard`. The installer creates the `journal/` and `visiond/` subdirectories once the mount exists.

## Step 4 — The repo and its two virtualenvs

```bash
git clone https://github.com/tigerstrake/doorboard.git ~/doorboard && cd ~/doorboard
UV_PROJECT_ENVIRONMENT=~/.venvs/doorboard uv sync --locked --no-dev
# door-visiond runs from its own venv so the HailoRT Python bindings (an apt
# package, not on PyPI) are visible to it and to nothing else:
uv venv --system-site-packages ~/.venvs/doorboard-visiond
UV_PROJECT_ENVIRONMENT=~/.venvs/doorboard-visiond uv sync --locked --no-dev
pnpm install --frozen-lockfile
VITE_AMBIENT_MOCK=false pnpm --filter @doorboard/door-ui build     # the kiosk bundle
```

`install-home-layout` refuses to run until both `~/.venvs/doorboard/bin/python` and `~/.venvs/doorboard-visiond/bin/python` exist and `apps/door-ui/dist/index.html` is built. *(Needs on-device verification: the exact visiond venv recipe for the Hailo bindings — confirm `~/.venvs/doorboard-visiond/bin/python -c "import hailo_platform"` works.)*

## Step 5 — Configuration

Nothing on the Pi pulls a config bundle from the NUC; `~/doorboard/.env` **is** the configuration.

```bash
cp deploy/pi-door/doorboard.env.example ~/doorboard/.env && chmod 600 ~/doorboard/.env
```

Fill every `CHANGE_ME`. The pairs that must match something elsewhere:

| Pi `.env` | Must equal |
|---|---|
| `SYNC_INGEST_TOKEN` | a token from the NUC: `docker compose … exec control-plane-api control-plane-api-admin issue-token --door-id primary --scope ingest` |
| `CONTROL_PLANE_URL` | `http://<nuc>:8090` |
| `DOOR_API_SOCIAL_ADMIN_TOKEN` | NUC `.env` `CONTROL_PLANE_DOOR_API_ADMIN_TOKEN` (video/ring-photo pull) |
| `DOOR_API_INTERNAL_EVENT_TOKEN` | itself — read by door-api and door-visiond from this one file; unset = recognised faces never reach the screen |
| `VISIOND_RELAY_BASE_URL` / `VISIOND_RELAY_DEVICE_TOKEN`, `DOOR_API_VISITOR_RELAY_BASE_URL` / `DOOR_API_VISITOR_RELAY_DEVICE_TOKEN` | `https://door.tigerstrake.com` and the two Cloudflare Pages secrets ([relay-cloudflare-deploy.md](relay-cloudflare-deploy.md)); leave unset for a door without the phone paths |
| `DOOR_API_VISITOR_PUBLIC_BASE_URL` | `http://door-pi.local` — the LAN proxy origin, nothing else listens on the LAN |
| `VISIOND_ENROLLMENT_ROOT` / `VISIOND_REQUIRE_ENCRYPTED_STORAGE=true` | the template's values; do not point the enrollment DB at the unencrypted SSD |

The installer renders this file into a systemd-safe copy (`deploy/pi-door/render-systemd-env`); after any later edit, re-run the installer or the renderer.

## Step 6 — Encrypted enrollment volume

Full detail in [enrollment-encrypted-volume.md](enrollment-encrypted-volume.md) Part B; the short form:

```bash
sudo install -m 0600 -o root -g root deploy/pi-door/enrollment-unlock.env.example /etc/doorboard/enrollment-unlock.env
sudo nano /etc/doorboard/enrollment-unlock.env    # ENROLLMENT_KEY_URL=http://<nuc>:8090/status/keys/enrollment
                                                  # ENROLLMENT_OWNER=<run-user>:<run-user>
sudo install -m 0600 -o root -g root /dev/stdin /etc/doorboard/enrollment-key.token <<<'<CONTROL_PLANE_ENROLLMENT_KEY_TOKEN>'
```

- **Reused SSD:** the container `/mnt/ssd/doorboard/enrollment.luks` is already there. Do not run `create-enrollment-volume` (it refuses anyway). The unlock unit installed in the next step opens it with the NUC's passphrase; the enrollments inside are intact.
- **New SSD:** `sudo ENROLLMENT_UNLOCK_ENV=/etc/doorboard/enrollment-unlock.env scripts/create-enrollment-volume`, then re-enroll (below).

## Step 7 — Install the units

```bash
sudo bash deploy/pi-door/install-home-layout ~/doorboard <run-user>
```

One command installs every unit with the home-layout drop-ins, the LAN proxy (`door-proxy.service`, `/etc/doorboard/Caddyfile`), the journald policy and the SSD journal mount, root-owned copies of the unlock/health-agent scripts under `/usr/local/lib/doorboard/`, renders `.env`, and enables everything including `doorboard-enrollment-unlock.timer`. It starts nothing; follow its printed adoption order (`systemctl start …` per unit, checking each `/health`), then `sudo journalctl --flush`.

## Step 8 — Kiosks

```bash
install -D -m 0755 deploy/pi-door/labwc-autostart.example ~/.config/labwc/autostart
```

labwc launches both kiosks at session start; each waits for door-api `/health` and relaunches Chromium on exit. Which output each window lands on is a labwc window rule (`~/.config/labwc/rc.xml`, matched by title) — on the live door HDMI-A-1 is the 7" touchscreen. Log the run user in automatically on the desktop (raspi-config → System → Auto Login) so a power cut brings the screens back without a keyboard.

## Verification

```bash
for p in 8080 8081 8082 8083; do curl -s 127.0.0.1:$p/health; echo; done   # + 8084 if door-voice is on
curl -s 127.0.0.1:8081/health | grep enrollment_locked      # false once the NUC has released the key
curl -s -o /dev/null -w '%{http_code}\n' http://door-pi.local/session   # 404: the proxy forwards only the allow-list
systemctl is-active door-api door-media door-visiond door-sync door-proxy doorboard-enrollment-unlock.timer
```

Then the door test: press the bell (chime + ring + doorpad into visitor mode), walk past the camera as an enrolled person (light + greeting), and finally the offline test — unplug the network, reboot, confirm both screens and the bell come up with nothing but the ESP32 and the Pi.

## Biometrics and re-enrollment

> [!IMPORTANT]
> Face embeddings never leave the door. They live in `/mnt/vision-enrollment/doorboard/enrollment.sqlite` on the LUKS volume; the passphrase lives only on the NUC (ADR-0009 §6). There is no backup, by design.

- **Reused SSD, NUC intact:** nothing to re-enroll. The volume reopens with the NUC key + the token file from Step 6.
- **New SSD, or the NUC's `CONTROL_PLANE_ENROLLMENT_KEY` is gone:** everyone re-enrolls. At the door: the admin panel at `http://127.0.0.1:5173/admin` on the Pi (or the DoorPad's own *Enroll my face*, ADR-0019). By phone: `scripts/enrollment-qr "Name"` from the laptop, which mints an invite through the relay ([remote-enrollment-relay.md](remote-enrollment-relay.md)). There is no `/admin/enrollment` route on door-api.
