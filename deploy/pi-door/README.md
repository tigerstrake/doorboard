# deploy/pi-door — door appliance deployment

Raspberry Pi OS 64-bit on the Pi 5. Hardware-facing services run under **systemd** (units in `infra/systemd/`), not containers, until container hardware access is proven (handoff §15). Tasks: T-000 stubs layout; T-703/T-702 harden.

## Boot contract

1. Cold boot → operational door with **no network and no NUC** (offline boot required).
2. systemd ordering: SSD verified writable and not the root filesystem
   (`doorboard-storage-ready.service`, `Requires=`/`After=` on every writer) →
   mediamtx + door-media + door-visiond + door-api → kiosk launch only after door-api
   `/health` answers. door-api does not wait for door-visiond, and nothing on its chain
   waits for the network (`tests/test_systemd_units.py`).
3. Every service: `Restart=on-failure` with no start-rate limit; journald → structured
   JSON logs, persistent and bounded (`journald.conf.d/doorboard.conf`) on the SSD
   (`var-log-journal.mount`), never the boot card (ADR-0007).
4. Chromium kiosks: two instances, HDMI-1 `/wallboard`, HDMI-2 `/doorpad`, crash-relaunch, no toolbars, no exit path from touch.
5. LAN surface (ADR-0047): every door service binds `127.0.0.1`; `door-proxy.service`
   (Caddy, `deploy/pi-door/Caddyfile`) is the only LAN listener and forwards only the
   visitor page, the tokenised visitor routes, the NUC's two media-inbox pulls and
   `/metrics/<service>`. Everything else is a 404 from the proxy. It is not on the door
   path — the kiosks talk to loopback. See
   [docs/runbooks/pi-lan-proxy.md](../../docs/runbooks/pi-lan-proxy.md).

## Installation

1. Create the `doorboard` system user and the `video`, `render`, and `dialout`
   group memberships. Install the repo at `/opt/doorboard` and create its uv
   environment with `uv sync --locked --no-dev`, install the pnpm workspace,
   and build the kiosk bundle with
   `VITE_AMBIENT_MOCK=false pnpm --filter @doorboard/door-ui build`. Set
   `VITE_AIRCRAFT_ALERT_DISTANCE_KM` at build time if the default 3 km hallway
   alert threshold is not appropriate for the deployment.
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
   the volume unlocks — or let `doorboard-enrollment-unlock.timer` do it. The NUC
   releases the key at `GET /status/keys/enrollment` to a holder of the narrow
   release token; the whole procedure is
   [docs/runbooks/enrollment-encrypted-volume.md](../../docs/runbooks/enrollment-encrypted-volume.md).
3. Install the pinned MediaMTX binary at `/usr/local/bin/mediamtx`. door-media
   writes the config it runs MediaMTX with at
   `/mnt/ssd/doorboard/mediamtx/mediamtx.yml` (`MEDIAMTX_CONFIG_PATH`) and
   spawns the process itself; `mediamtx.service` is the standalone alternative
   and must point at that same file (the home-layout drop-in does). Running
   both is the known two-instance item in `docs/audit-findings-todo.md`.
   Install camera/Hailo/UART packages
   required by the selected hardware adapters, and `apt install caddy` for the
   LAN proxy (the installer disables the package's own `caddy.service`, which
   would otherwise hold port 80). Optionally `apt install
   prometheus-node-exporter` for the NUC's thermal panel; the installer runs it
   on loopback behind the proxy (`infra/monitoring/README.md`).
4. Create `/etc/doorboard/doorboard.env` from
   `doorboard.env.example`, replace every `CHANGE_ME`, and keep it root-owned.
   It contains only Pi-scoped and local service credentials, never NUC admin,
   NAS admin, Home Assistant, or worker secrets.
5. Create the two kiosk environment files from the examples, then run
   `sudo bash deploy/pi-door/install-systemd /opt/doorboard`. It also copies
   the two scripts root runs (LUKS unlock, health agent) to
   `/usr/local/lib/doorboard/` and enables the unlock timer. Kiosks: either
   enable the units after the graphical session is configured
   (`sudo systemctl enable chromium-kiosk@wallboard chromium-kiosk@doorpad`;
   Wayland env in `kiosk-*.env`), or, on a labwc session as the live Pi runs,
   install `labwc-autostart.example` as `~/.config/labwc/autostart` — it waits
   for door-api `/health` and relaunches each kiosk on exit.
6. Reboot with the NUC/network disconnected and verify the loopback `/health`
   endpoints (door-api 8080, door-visiond 8081, door-media 8082, door-sync 8083,
   door-voice 8084 when enabled), both kiosks, UART feedback, and local recording.

Recovery/replacement procedures live in `docs/runbooks/`.
