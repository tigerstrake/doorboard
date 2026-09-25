# infra/systemd

systemd units for the door Pi: `door-visiond`, `door-media`, `door-api`,
`door-sync`, `door-voice`, the built `door-ui`, `mediamtx`, `door-proxy`
(the one LAN listener, ADR-0047), `node-exporter` (loopback host metrics),
`chromium-kiosk@wallboard`, `chromium-kiosk@doorpad`, the LUKS unlock and its
retry timer, and a timer-driven health agent. Ordering encodes the boot
contract in `deploy/pi-door/README.md`: a non-root writable SSD is verified
first, nothing on door-api's chain waits for the network or the NUC
(`tests/test_systemd_units.py` pins it), and kiosks use a bounded loopback
health wait. Services use `Restart=on-failure` with no start-rate limit, so a
crash loop keeps retrying instead of parking in `failed`; three consecutive
failed local health probes restart a hung-but-active unit, and the agent
starts a unit it finds `failed`. The two units that run as root execute
root-owned copies of their scripts from `/usr/local/lib/doorboard/`.

Install with `sudo bash deploy/pi-door/install-systemd /opt/doorboard` after
creating `/etc/doorboard/doorboard.env` from the Pi-only template. Kiosk units
also require `/etc/doorboard/kiosk-wallboard.env` and
`/etc/doorboard/kiosk-doorpad.env`.
