# door-media — local media state and MediaMTX integration

**Plane:** real-time door · **Host:** door Pi 5 · **Language:** Python · **Tasks:** T-201, T-203

Owns the MediaMTX instance and the recording lifecycle. The visitor camera publishes to MediaMTX continuously (one H.264 encode, many consumers); door-media controls recording windows, finalizes clips, generates thumbnails, enforces retention, and hands finalized artifacts to door-sync.

## Flow

```text
visitor cam + mic → rpicam/libcamera → H.264/AAC → MediaMTX
  ├→ WebRTC (kiosks, phones on local network; primary live protocol — not HLS)
  └→ segmented recording on USB SSD
        → on session events: cut/finalize clip (bell_clip | video_message)
        → on explicit photo-booth trigger: capture still → local review → keep/discard
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
- HTTP: `/health`, `/metrics`, `GET /streams` (endpoint metadata for UIs), `GET /recordings` + `DELETE /recordings/{id}` (admin), and `/photos/*` still-capture review endpoints used by the feature-gated DoorPad photo booth.
- MediaMTX sits behind a `MediaRouter` adapter (`mediamtx | mock`); raw MediaMTX/RTSP ports are never exposed off-host (security §16).

## Key metrics

`stream_up`, `webrtc_clients`, `recording_write_mbps`, `ssd_free_bytes`, `sync_queue_depth`, `oldest_unsynced_s`.

## Retention Policies

Retention is configured globally and per-kind in the environment files (e.g. `.env.example`). The default values are:

- **Global SSD Minimum Free Space:** `4 GiB` (`DOOR_MEDIA_MIN_FREE_BYTES=4294967296`). If free space falls below this, new recordings are blocked to preserve system stability.
- **Global SSD Maximum Recording Space:** `48 GiB` (`DOOR_MEDIA_MAX_RECORDING_BYTES=51539607552`).
- **Bell Clips:**
  - Max Age: `3 days` (`DOOR_MEDIA_BELL_CLIP_MAX_AGE_S=259200`)
  - Max Storage Size: `10 GiB` (`DOOR_MEDIA_BELL_CLIP_MAX_SIZE_BYTES=10737418240`)
- **Video Messages:**
  - Max Age: `14 days` (`DOOR_MEDIA_VIDEO_MESSAGE_MAX_AGE_S=1209600`)
  - Max Storage Size: `30 GiB` (`DOOR_MEDIA_VIDEO_MESSAGE_MAX_SIZE_BYTES=32212254720`)
- **Photo Booth Clips:**
  - Max Age: `7 days` (`DOOR_MEDIA_PHOTO_BOOTH_MAX_AGE_S=604800`)
  - Max Storage Size: `8 GiB` (`DOOR_MEDIA_PHOTO_BOOTH_MAX_SIZE_BYTES=8589934592`)

A clip is only deleted due to age or size caps if it is marked as synced (door-sync has verified checksum upload). Deletion unlinks both the video clip and its thumbnail from the SSD.

Photo booth stills are explicit-capture only. Review captures live under `photo-review/` and are not inserted in the durable registry until the visitor keeps them. Saving writes the photo, thumbnail, and consent sidecar under `SSD_DATA_ROOT`; discarding removes the review file and leaves no registry row or sync-visible artifact.

## Thumbnail Heuristic

Thumbnails are generated automatically using `ffmpeg` when a recording is finalized. The frame-grab offset is determined dynamically based on the clip's duration:
- Offset is selected at `1.0s` to capture stable video and avoid startup black frames/fade-in.
- If the clip is shorter than `2.0s`, it grabs the frame at half the duration (`duration_s / 2.0`).
- If the duration is `0.0` or less, it grabs the frame at `0.0s`.
- If thumbnail generation fails, finalization still succeeds, and the thumbnail path is marked as missing in the database.
