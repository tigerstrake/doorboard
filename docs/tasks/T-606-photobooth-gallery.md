# T-606: Photo booth + private gallery

**Agent:** codex · **Milestone:** M6 · **Depends on:** T-201, T-502
**Why this agent:** media-pipeline work (capture, consent metadata, sync, deletion) where privacy handling must be exact.

## Context

Handoff §13 (photo booth/gallery); spec: [integrations/gallery/README.md](../../integrations/gallery/README.md). Explicit capture only; private sharing only in v1.

## Deliverables

- DoorPad photo-booth flow (feature-flagged): explicit trigger → countdown → capture via door-media (visitor camera) → review (keep/retake/discard) → save with consent metadata; discard leaves zero files (same standard as T-402).
- door-media photo support: still capture path, thumbnails, retention/sync integration (photos are media artifacts like clips).
- `GalleryStore` (`nas`/`mock`): approved-photo ingestion via door-sync, album structure, manual tagging (MVP), deletion honoring `social.deletion_requested` end-to-end (SSD copy, NAS copy, thumbnails).
- Admin gallery panel: approve/tag/delete; approved photos optionally surfaceable as a wallboard "moments" tile (owner-approved only).
- Tests: consent metadata presence, discard-leaves-nothing, deletion propagation across tiers.

## Out of scope

Public/social posting (deferred; needs its own ADR), face recognition on photos (never), separate booth hardware (uses door cameras/flow for v1 unless owner adds hardware later).

## Acceptance criteria

- Full flow on bench: capture → review → save → synced to NAS gallery → visible in admin; deletion removes every copy (verified on disk both sides).
- Wallboard shows only owner-approved photos; unapproved photos unreachable from any public route (test).
- Feature flag off → no booth button anywhere, no dormant public endpoints.
