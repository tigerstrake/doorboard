# deploy/pi-door — door appliance deployment

Raspberry Pi OS 64-bit on the Pi 5. Hardware-facing services run under **systemd** (units in `infra/systemd/`), not containers, until container hardware access is proven (handoff §15). Tasks: T-000 stubs layout; T-703/T-702 harden.

## Boot contract

1. Cold boot → operational door with **no network and no NUC** (offline boot required).
2. systemd ordering: SSD mount verified (`ssd.mount` dependency) → door-media + door-visiond + door-api → kiosk launch only after door-api `/health` is ok.
3. Every service: `Restart=on-failure`, hardware watchdog enabled, journald → structured logs.
4. Chromium kiosks: two instances, HDMI-1 `/wallboard`, HDMI-2 `/doorpad`, crash-relaunch, no toolbars, no exit path from touch.

## Installation

1. Create the `doorboard` system user and the `video`, `render`, and `dialout`
   group memberships. Install the repo at `/opt/doorboard` and create its uv
   environment with `uv sync --locked --no-dev`, install the pnpm workspace,
   and build the kiosk bundle with `pnpm --filter @doorboard/door-ui build`.
2. Mount the SSD below `/mnt/ssd` and the limited NAS share at
   `/mnt/nas/doorboard`. The storage gate refuses to run services when
   `SSD_DATA_ROOT` resolves to the root filesystem.
   Provision a separate LUKS2 volume at `/mnt/vision-enrollment` for biometric
   enrollment data. Its key must be retrieved from the NUC into RAM at boot;
   do not persist the key on the Pi or SSD. Set `VISIOND_ENROLLMENT_ROOT` and
   `VISIOND_REQUIRE_ENCRYPTED_STORAGE=true`. Until that device-mapper mount is
   available, door-visiond runs in generic-greeting mode, reports
   `enrollment_locked`, refuses enrollment endpoints, and writes no biometric
   database to the unencrypted parent filesystem. Restart door-visiond after
   the volume unlocks. The NUC key-release endpoint remains part of hardware
   bring-up issue #84 because adding a new credential scope requires the
   contract/security escalation mandated by `AGENTS.md`.
3. Install the pinned MediaMTX binary at `/usr/local/bin/mediamtx` and its
   config at `/etc/doorboard/mediamtx.yml`. Install camera/Hailo/UART packages
   required by the selected hardware adapters.
4. Create `/etc/doorboard/doorboard.env` from
   `doorboard.env.example`, replace every `CHANGE_ME`, and keep it root-owned.
   It contains only Pi-scoped and local service credentials, never NUC admin,
   NAS admin, Home Assistant, or worker secrets.
5. Create the two kiosk environment files from the examples, then run
   `sudo bash deploy/pi-door/install-systemd /opt/doorboard`. Enable the kiosk
   instances after the graphical session is configured:
   `sudo systemctl enable chromium-kiosk@wallboard chromium-kiosk@doorpad`.
6. Reboot with the NUC/network disconnected and verify the four loopback
   `/health` endpoints, both kiosks, UART feedback, and local recording.

Recovery/replacement procedures live in `docs/runbooks/`.
