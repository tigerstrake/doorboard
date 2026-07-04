# infra/systemd

systemd units for the door Pi: `door-visiond`, `door-media`, `door-api`, `door-sync`, `mediamtx`, `chromium-kiosk@wallboard`, `chromium-kiosk@doorpad`, watchdog/health agent. Ordering encodes the boot contract in `deploy/pi-door/README.md` (SSD mount → services → kiosks after health-ok). `Restart=on-failure` everywhere; offline boot required.
