# door-visiond — real-time vision service

**Plane:** real-time door · **Host:** door Pi 5 · **Language:** Python · **Tasks:** T-302, T-303 (Codex), spec guarded by T-301 (Claude)

Sole owner of the Hailo accelerator and the recognition camera (ADR-0004). Detects faces, matches against the small enrolled set, maintains the short-lived `current_visitor` cache, and pushes profile updates to the ESP32.

## Pipeline (hardware mode)

```text
recognition camera (libcamera) → face detect → landmarks/align → embed (Hailo)
  → match vs enrollment DB (SQLite on SSD)
  → stability filter: min face size, match in 2 of last 3 frames
  → current_visitor cache (TTL 2.5 s) → vision.identity_stable event
  → door.profile_update to ESP32 (profile_id + TTL only)
```

Greeting cooldown 30 s per person. Unknown faces: `vision.face_visible` only — no identity event, no persisted embedding, ever (ADR-0005).

## Modes

`disabled | mock | single-camera | dual-camera | hardware` via `VISION_MODE`. Mock mode emits scripted identity events for dev/CI/simulator. Startup performs a Hailo runtime/model compatibility check; on failure it degrades to `disabled` (generic greetings) and reports via health — it never blocks the door.

## Interfaces

- Events out: `vision.face_visible`, `vision.identity_stable`, `vision.identity_expired`, `vision.privacy_mode_changed`, `vision.pipeline_status`; `door.profile_update`/`door.profile_clear` via Esp32Transport.
- HTTP: `GET /health`, `GET /metrics`, `GET /current-visitor`, `POST /enroll`, `POST /unenroll`, `POST /privacy-mode` (enroll/unenroll/privacy are admin-authenticated).
- Reads: enrollment DB (SSD), pinned model files, config from `packages/config`.

## Must never

Block button interaction; wait on the NUC; persist unknown embeddings; log biometric data; make or influence any access/security decision; share the Hailo device with another process.

## Key metrics

`inference_ms` (p50/p95), `face_to_identity_ms`, `cache_hit_rate`, `fps`, `frame_drops`, `hailo_ok`.
