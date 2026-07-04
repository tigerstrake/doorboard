# deploy/pi-door — door appliance deployment

Raspberry Pi OS 64-bit on the Pi 5. Hardware-facing services run under **systemd** (units in `infra/systemd/`), not containers, until container hardware access is proven (handoff §15). Tasks: T-000 stubs layout; T-703/T-702 harden.

## Boot contract

1. Cold boot → operational door with **no network and no NUC** (offline boot required).
2. systemd ordering: SSD mount verified (`ssd.mount` dependency) → door-media + door-visiond + door-api → kiosk launch only after door-api `/health` is ok.
3. Every service: `Restart=on-failure`, hardware watchdog enabled, journald → structured logs.
4. Chromium kiosks: two instances, HDMI-1 `/wallboard`, HDMI-2 `/doorpad`, crash-relaunch, no toolbars, no exit path from touch.

## Contents (as implemented)

- Provisioning script/checklist: OS flash, SSD setup, camera enable, Hailo runtime install (pinned version), UART enable, user/permissions (no default pi user, SSH keys only, USB/microSD physically hidden per trust model).
- Service install/update scripts (pull versioned release, restart units).
- `.env` template referencing only the Pi-scoped variables from `.env.example`.

Recovery/replacement procedures live in `docs/runbooks/`.
