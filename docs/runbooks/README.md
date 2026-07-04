# Runbooks

Operational procedures, written in M7 (task T-703) but stubbed as soon as the subsystem lands. Each runbook: symptoms → diagnosis → step-by-step fix → verification, executable by a sleep-deprived owner at 2 AM.

Required set (handoff §19.13):

- `boot-and-recovery.md` — cold boot, offline boot, kiosk stuck, watchdog loops
- `pi-replacement.md` — reflash microSD, restore config bundle, re-enroll faces (biometrics don't restore from backup — they never left the old SSD)
- `esp32-repair.md` — reflash firmware, link diagnostics, fallback behavior verification
- `storage-full.md` — SSD pressure, retention override, safe manual cleanup
- `network-outage.md` — LAN/VLAN issues, degraded-mode expectations, what queues where
- `nuc-outage.md` — planned maintenance procedure, what keeps working, recovery order
- `nas-backup-restore.md` — backup schedule, restore test procedure
- `token-rotation.md` — rotating the Pi's limited credentials after suspected theft
- `security-checklist.md` — deployment security checklist (T-701 output)
