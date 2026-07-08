# Runbooks

Operational procedures, written in M7 (task T-703) but stubbed as soon as the subsystem lands. Each runbook: symptoms → diagnosis → step-by-step fix → verification, executable by a sleep-deprived owner at 2 AM.

## Required Set (handoff §19.13)

- [boot-and-recovery.md](boot-and-recovery.md) — cold boot, offline boot, kiosk stuck, watchdog loops (**Verified 2026-07-08**)
- [pi-replacement.md](pi-replacement.md) — reflash microSD, restore config bundle, re-enroll faces (**Verified 2026-07-08**)
- [esp32-repair.md](esp32-repair.md) — reflash firmware, link diagnostics, fallback behavior verification (**Verified 2026-07-08**)
- [storage-full.md](storage-full.md) — SSD pressure, retention override, safe manual cleanup (**Verified 2026-07-08**)
- [network-outage.md](network-outage.md) — LAN/VLAN issues, degraded-mode expectations, what queues where (**Verified 2026-07-08**)
- [nuc-outage.md](nuc-outage.md) — planned maintenance procedure, what keeps working, recovery order (**Verified 2026-07-08**)
- [nas-backup-restore.md](nas-backup-restore.md) — database backup schedule and restore test procedure (**Verified 2026-07-08**)
- [token-rotation.md](token-rotation.md) — rotating the Pi's limited credentials after suspected theft (**Verified 2026-07-08**)
- [backup-procedures.md](backup-procedures.md) — overall system backup cadence, scopes, and recovery guidelines (**Verified 2026-07-08**)
- [monitoring-dashboards.md](monitoring-dashboards.md) — metrics catalog, health endpoints, and log event reference (**Verified 2026-07-08**)
- `security-checklist.md` — deployment security checklist (T-701 output, owned by T-701)
