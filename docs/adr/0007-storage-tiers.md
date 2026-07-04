# ADR-0007: Storage tiers — microSD boots, SSD works, NAS remembers

**Status:** Accepted · **Date:** 2026-07-04

## Context

microSD cards die under continuous video/database writes. The Pi's PCIe lane is taken by the Hailo HAT (ADR-0004), ruling out NVMe. The door Pi is stealable, so it must not hold the long-term archive.

## Decision

| Tier | Medium | Holds | Never holds |
|---|---|---|---|
| Boot | 128 GB microSD | OS, recovery, minimal config | recordings, databases, queues |
| Active | USB 3 SATA SSD (≥256 GB) | MediaMTX segments, finalized clips awaiting upload, SQLite (WAL) DBs, enrollment data, thumbnails, media cache | long-term archive |
| Archive | NAS | completed clips, photos, gallery, DB backups | active databases, live recording targets |

Retention on the SSD is bounded: door-media enforces a size/age cap and stops recording safely (interaction preserved, control plane alerted) when free space crosses the threshold. door-sync deletes local media only after checksum-verified upload. All service data paths derive from `SSD_DATA_ROOT`; writing recordings or active DBs to a microSD path is a review-blocking defect.

## Consequences

- Deployment scripts must verify the SSD is mounted before starting door-media/door-visiond (systemd dependencies).
- Storage metrics (free space, queue age) are mandatory and surfaced in the admin UI.
- A stolen Pi loses at most: recent unsynced clips, the enrollment set (small, consented), and limited-scope tokens — which are revocable from the NUC.
