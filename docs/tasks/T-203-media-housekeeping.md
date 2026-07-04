# T-203: Thumbnails, retention config, storage visibility

**Agent:** gemini · **Milestone:** M2 · **Depends on:** T-201
**Why this agent:** well-bounded feature work on top of T-201's engine.

## Context

Rounds out door-media per its README: operators need to *see* storage state, and clips need thumbnails for the admin inbox and (later) wallboard moments.

## Deliverables

- Thumbnail generation on `media.recording_finalized` (ffmpeg frame-grab, documented offset heuristic), `media.thumbnail_ready` emission, thumbnails stored beside clips under `SSD_DATA_ROOT`, cleaned up with their clip.
- Retention configuration surface: typed config (packages/config) for size/age caps and per-kind policy (`bell_clip` vs `video_message` may differ); defaults documented in door-media README.
- Storage status: periodic `media.storage_status` emission; ui-kit gauge components (free space, queue depth, oldest-unsynced age) wired into the admin route placeholder and available for T-703 dashboards.
- `GET /recordings` filtering/pagination per api-conventions; recording list UI table (admin placeholder) with thumbnail, kind, sync state, delete button (calls existing DELETE).
- Tests: thumbnail failure doesn't block finalization (event still emitted, thumb marked missing); retention respects per-kind policy; deletion removes clip+thumb atomically.

## Out of scope

Recording/finalization engine changes (escalate if it lacks a hook you need), sync logic, NAS anything, admin auth wiring (use the existing placeholder pattern).

## Acceptance criteria

- Finalized clip → thumbnail within 5 s in bench test; corrupted-input test produces graceful failure path.
- Storage gauges update live in UI during a simulated fill; values match `df`-verified reality within tolerance.
- All new config appears in `.env.example` with comments.
