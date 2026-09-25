# Runbooks

Operational procedures, written in M7 (task T-703) but stubbed as soon as the subsystem lands. Each runbook: symptoms → diagnosis → step-by-step fix → verification, executable by a sleep-deprived owner at 2 AM.

Every runbook carries a **`Verified-on:`** line naming where it was last walked — *simulator*, *door-pi (home layout)*, *NUC* — and when. A 2026-07-08 simulator stamp predates the hardware and the home layout; it means the commands were right against the code then, not that they have been run on the door. Re-walk on the device and update the line.

## Required Set (handoff §19.13)

- [boot-and-recovery.md](boot-and-recovery.md) — cold boot, offline boot, kiosk stuck, unexplained reboots (**Verified-on:** simulator 2026-07-08; re-checked against the units 2026-09-25)
- [pi-replacement.md](pi-replacement.md) — reflash, storage, the two venvs, `.env`, the encrypted enrollment volume, `install-home-layout`, kiosks, re-enrollment (**Draft — re-walk on hardware**)
- [esp32-repair.md](esp32-repair.md) — reflash firmware, link diagnostics, fallback behavior verification (**Verified-on:** simulator 2026-07-08)
- [storage-full.md](storage-full.md) — SSD pressure, retention override, safe manual cleanup (**Verified-on:** simulator 2026-07-08; commands re-checked 2026-09-09)
- [network-outage.md](network-outage.md) — LAN/VLAN issues, degraded-mode expectations, what queues where (**Verified-on:** simulator 2026-07-08)
- [nuc-outage.md](nuc-outage.md) — planned maintenance procedure, what keeps working, recovery order (**Verified-on:** simulator + compose 2026-07-08)
- [nas-backup-restore.md](nas-backup-restore.md) — database backup schedule and restore test procedure (**Verified-on:** simulator + local Postgres 2026-07-08)
- [token-rotation.md](token-rotation.md) — rotating the Pi's limited credentials after suspected theft (**Verified-on:** simulator 2026-07-08)
- [backup-procedures.md](backup-procedures.md) — overall system backup cadence, scopes, and recovery guidelines (**Verified-on:** simulator 2026-07-08; re-checked 2026-09-25)
- [monitoring-dashboards.md](monitoring-dashboards.md) — metrics catalog, health endpoints, alert rules, and log event reference (rewritten from the source 2026-09-25; Pi scrape path **needs on-device verification**)
- [security-checklist.md](security-checklist.md) — deployment security checklist (T-701; re-audited 2026-09-24, deploy walk pending)

## Additional

- [enrollment-encrypted-volume.md](enrollment-encrypted-volume.md) — LUKS enrollment volume with key-on-NUC (ADR-0009 §6, T-302) (**Verified-on:** door-pi + NUC 2026-07-27)
- [remote-enrollment-relay.md](remote-enrollment-relay.md) — phone-enrollment relay on Cloudflare: health checks, symptoms, sealing-key rotation, disabling it (ADR-0016, ADR-0043) (**Verified-on:** simulator; relay per its deploy runbook)
- [relay-cloudflare-deploy.md](relay-cloudflare-deploy.md) — deploying the relay to Cloudflare Pages + D1, verifying it, and decommissioning the Vercel era (ADR-0043)
- [visitor-qr.md](visitor-qr.md) — the wallboard visitor QR: which target is live, why it fell back to LAN, why a note did not appear (ADR-0017, T-307) (**Verified-on:** simulator; LAN fallback through the proxy pending)
- [pi-lan-proxy.md](pi-lan-proxy.md) — the door Pi's one LAN listener: what the proxy forwards, what it 404s, what has to agree with it (ADR-0047) (**Draft — needs on-device verification**)
