# door-media — local media state and MediaMTX integration

**Plane:** real-time door · **Host:** door Pi 5 · **Language:** Python · **Tasks:** T-201, T-203

Owns the MediaMTX instance and the recording lifecycle. The visitor camera publishes to MediaMTX continuously (one H.264 encode, many consumers); door-media controls recording windows, finalizes clips, generates thumbnails, enforces retention, and hands finalized artifacts to door-sync.

## Flow

```text
visitor cam + mic → rpicam/libcamera → H.264/AAC → MediaMTX
  ├→ WebRTC (kiosks, phones on local network; primary live protocol — not HLS)
  └→ segmented recording on USB SSD
        → on session events: cut/finalize clip (bell_clip | video_message)
        → sha256 + thumbnail → media.recording_finalized → sync queue
```

## Hard requirements

- Stream is live **before** any bell press; no cold-start capture/encode on press (bell → recording event < 500 ms).
- Recording never blocks live playback.
- Record locally first; never wait synchronously on NAS/NUC in the visitor path.
- All writes under `SSD_DATA_ROOT` (ADR-0007). Bounded retention: enforce size/age caps; when storage is low, stop recording safely, keep interaction alive, emit `system.storage_alert`.
- Local deletion of a synced clip only after door-sync confirms checksum-verified upload.

## Interfaces

- Events out: `media.recording_started/_finalized`, `media.thumbnail_ready`, `media.retention_deleted`, `media.storage_status`.
- Events in: `session.state_changed` (record window triggers), deletion requests.
- HTTP: `/health`, `/metrics`, `GET /streams` (endpoint metadata for UIs), `GET /recordings` + `DELETE /recordings/{id}` (admin).
- MediaMTX sits behind a `MediaRouter` adapter (`mediamtx | mock`); raw MediaMTX/RTSP ports are never exposed off-host (security §16).

## Key metrics

`stream_up`, `webrtc_clients`, `recording_write_mbps`, `ssd_free_bytes`, `sync_queue_depth`, `oldest_unsynced_s`.
