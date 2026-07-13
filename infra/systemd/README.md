# infra/systemd

systemd units for the door Pi: `door-visiond`, `door-media`, `door-api`,
`door-sync`, the built `door-ui`, `mediamtx`, `chromium-kiosk@wallboard`,
`chromium-kiosk@doorpad`, and a timer-driven health agent. Ordering encodes
the boot contract in `deploy/pi-door/README.md`: a non-root writable SSD is
verified first, local services start without waiting for the network or NUC,
and kiosks use a bounded loopback health wait. Services use
`Restart=on-failure`; three consecutive failed local health probes trigger a
restart for hung processes.

Install with `sudo bash deploy/pi-door/install-systemd /opt/doorboard` after
creating `/etc/doorboard/doorboard.env` from the Pi-only template. Kiosk units
also require `/etc/doorboard/kiosk-wallboard.env` and
`/etc/doorboard/kiosk-doorpad.env`.
