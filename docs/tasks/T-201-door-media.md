# T-201: door-media — MediaMTX integration + SSD recording

**Agent:** codex · **Milestone:** M2 · **Depends on:** T-002, T-003
**Why this agent:** the media pipeline has hard no-cold-start/no-blocking requirements and storage-safety invariants.

## Context

Spec: [apps/door-media/README.md](../../apps/door-media/README.md), flow: ARCHITECTURE.md §6, storage rules: ADR-0007. One-camera configuration first (`single_camera_mode`) per handoff M2.

## Deliverables

- door-media FastAPI service: `/health`, `/metrics`, `GET /streams`, `GET/DELETE /recordings` (admin-auth), emitting all `media.*` events.
- `MediaRouter` adapter, `mediamtx` implementation: manage MediaMTX config/lifecycle (pinned version), camera publish path via libcamera/`rpicam-vid` (H.264, one encode), WebRTC (WHEP) read path, segmented recording to `SSD_DATA_ROOT`.
- Recording lifecycle: session-event-triggered record windows (`bell_clip`, `video_message`), finalization (remux/cut, sha256, duration/size), thumbnail hook (T-203 fills implementation), `media.recording_finalized` handoff for sync.
- Retention engine: size/age caps, `recording_allowed=false` + safe stop + `system.storage_alert` under low space; deletion only of synced-or-expired items per policy.
- Startup: stream up before any session activity; MediaMTX supervised (restart, health sub-check `stream_up`).
- Mock mode (from T-003's stub) kept working — CI path.

## Out of scope

UI playback (T-202), thumbnails/storage tiles polish (T-203), sync/upload (T-502), two-camera recognition feed (T-302 owns its camera), audio tuning beyond capture-what-works.

## Acceptance criteria

- On Pi bench: bell-trigger → `media.recording_started` < 500 ms with stream pre-live (measured via T-104 conventions); recording during active WebRTC playback causes no playback stall.
- Kill/restore MediaMTX → service degrades honestly (health, events) and recovers without reboot.
- Storage-full simulation: recording stops safely, interaction events keep flowing, alert emitted, no partial-file corruption (integrity test on finalized clips).
- No MediaMTX/RTSP port reachable from off-host in the shipped config.
