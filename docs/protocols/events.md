# Event catalog (normative)

This is the specification `packages/contracts` implements (task T-002). All inter-service messages use these shapes. Ad hoc JSON is forbidden (ADR-0003). Changes to this file require an ADR.

## Envelope

Every event carries:

| Field | Type | Notes |
|---|---|---|
| `event_id` | UUIDv7 | unique per event; UUIDv7 gives time-ordering for free |
| `type` | string | dot-namespaced, from this catalog |
| `source` | string | emitting service name, e.g. `door-visiond` |
| `occurred_at` | ISO-8601 UTC | wall clock, display/audit only |
| `monotonic_ms` | int | emitter's monotonic clock; use for latency and expiry math |
| `door_id` | string | `"primary"` for v1 |
| `trace_id` | UUID | propagated across the whole interaction chain |
| `payload` | object | typed per event, below |

Rules: UTC only; never compute durations from `occurred_at`; `person_id` is opaque and never a display name; unknown faces never produce events containing biometric or identifying data.

## door.* — physical I/O (ESP32 ↔ Pi)

| Type | Direction | Payload |
|---|---|---|
| `door.button_pressed` | ESP32 → Pi | `press_id: uuid`, `had_cached_profile: bool`, `profile_id: string\|null` |
| `door.knock_detected` | ESP32 → Pi | `pattern_id: string` (matched pattern name), `confidence: float` |
| `door.contact_changed` | ESP32 → Pi | `state: "open"\|"closed"` |
| `door.profile_update` | Pi → ESP32 | `profile_id: string`, `expires_at_monotonic_ms: int`, `priority: "normal"\|"high"` — see wire protocol doc |
| `door.profile_clear` | Pi → ESP32 | `reason: "expired"\|"privacy_mode"\|"admin"` |
| `door.effect_play` | Pi → ESP32 | `effect_id: string` (LED/audio effect from pre-flashed set), `duration_ms: int` |
| `door.controller_health` | ESP32 → Pi | `uptime_s: int`, `fw_version: string`, `cached_profile_id: string\|null`, `fallback_active: bool` |

## vision.* — recognition (door-visiond)

| Type | Payload |
|---|---|
| `vision.face_visible` | `face_count: int`, `largest_face_px: int` — no identity, no embedding |
| `vision.identity_stable` | `person_id: opaque`, `display_name: string`, `confidence: float`, `expires_at: ISO-8601`, `expires_at_monotonic_ms: int`, `profile_id: string` |
| `vision.identity_expired` | `person_id: opaque` |
| `vision.privacy_mode_changed` | `enabled: bool`, `changed_by: "admin"\|"schedule"\|"physical"` |
| `vision.pipeline_status` | `mode: "disabled"\|"mock"\|"single-camera"\|"dual-camera"\|"hardware"`, `hailo_ok: bool`, `fps: float`, `inference_ms_p50: float` |

Only enrolled, consenting people ever appear in `identity_stable`. There is no event type for "unknown person identified" by design.

## session.* — visitor session (door-api)

| Type | Payload |
|---|---|
| `session.state_changed` | `session_id: uuid`, `from_state: State`, `to_state: State`, `trigger: string` |
| `session.started` | `session_id`, `entry: "button"\|"touch"\|"approach"` |
| `session.ended` | `session_id`, `outcome: "answered"\|"unanswered_timeout"\|"message_left"\|"abandoned"\|"reset"` |

`State` enum (normative, owned by door-api):
`IDLE, APPROACH_DETECTED, IDENTITY_CACHED, BUTTON_PRESSED, VISITOR_MODE, RINGING, ANSWERED, UNANSWERED_TIMEOUT, VIDEO_MESSAGE_OFFERED, VIDEO_MESSAGE_RECORDING, VIDEO_MESSAGE_REVIEW, VIDEO_MESSAGE_SAVED, SESSION_END`

Transition requirements: `BUTTON_PRESSED → VISITOR_MODE` is immediate and local; every transition emits `session.state_changed`; sessions auto-expire back to `IDLE`; reloading a kiosk browser must rejoin the current session, not create one.

Legal transition table:

| From state | To states |
|---|---|
| `IDLE` | `APPROACH_DETECTED`, `BUTTON_PRESSED` |
| `APPROACH_DETECTED` | `IDENTITY_CACHED`, `BUTTON_PRESSED`, `IDLE` |
| `IDENTITY_CACHED` | `BUTTON_PRESSED`, `IDLE`, `APPROACH_DETECTED` |
| `BUTTON_PRESSED` | `VISITOR_MODE`, `SESSION_END` |
| `VISITOR_MODE` | `RINGING`, `SESSION_END` |
| `RINGING` | `ANSWERED`, `UNANSWERED_TIMEOUT`, `SESSION_END` |
| `ANSWERED` | `VIDEO_MESSAGE_OFFERED`, `SESSION_END` |
| `UNANSWERED_TIMEOUT` | `VIDEO_MESSAGE_OFFERED`, `SESSION_END` |
| `VIDEO_MESSAGE_OFFERED` | `VIDEO_MESSAGE_RECORDING`, `SESSION_END` |
| `VIDEO_MESSAGE_RECORDING` | `VIDEO_MESSAGE_REVIEW`, `SESSION_END` |
| `VIDEO_MESSAGE_REVIEW` | `VIDEO_MESSAGE_SAVED`, `VIDEO_MESSAGE_RECORDING`, `SESSION_END` |
| `VIDEO_MESSAGE_SAVED` | `SESSION_END` |
| `SESSION_END` | `IDLE` |

## media.* — recording lifecycle (door-media)

| Type | Payload |
|---|---|
| `media.recording_started` | `recording_id: uuid`, `session_id`, `kind: "bell_clip"\|"video_message"\|"photo_booth"`, `stream: string` |
| `media.recording_finalized` | `recording_id`, `path: string` (SSD-relative), `duration_s: float`, `size_bytes: int`, `sha256: string`, `consent_context: "visitor_initiated"\|"bell_event"` |
| `media.thumbnail_ready` | `recording_id`, `path: string` |
| `media.retention_deleted` | `recording_id`, `reason: "age"\|"space"\|"user_request"\|"synced"` |
| `media.storage_status` | `free_bytes: int`, `queue_depth: int`, `oldest_unsynced_s: int`, `recording_allowed: bool` |

## sync.* — Pi → NUC/NAS bridge (door-sync)

| Type | Payload |
|---|---|
| `sync.upload_queued` | `item_id: uuid`, `recording_id`, `target: "nas"\|"nuc"` |
| `sync.upload_completed` | `item_id`, `verified_sha256: string`, `attempts: int` |
| `sync.upload_failed` | `item_id`, `attempts: int`, `next_retry_at: ISO-8601`, `error_class: string` |

Local file deletion is only legal after `sync.upload_completed` with a matching checksum.

## status.* — presence / Weasley clock (control-plane-api)

| Type | Payload |
|---|---|
| `status.presence_changed` | `subject_id: opaque` (owner/roommate), `label: "available"\|"busy"\|"do_not_disturb"\|"sleeping"\|"at_class"\|"at_library"\|"away"\|"unknown"`, `source: "manual"\|"focus_shortcut"\|"geofence_label"\|"calendar"\|"default"`, `until: ISO-8601\|null` |

Precedence (higher wins): manual > focus_shortcut > geofence_label > calendar > default. Broad labels only — no coordinates, no calendar contents.

## social.* — voluntary interactions (door-api public write, control-plane-api storage)

| Type | Payload |
|---|---|
| `social.guestbook_entry_created` | `entry_id: uuid`, `text: string` (sanitized, length-capped), `author_label: string\|null` (freeform, optional) |
| `social.poll_vote_cast` | `poll_id`, `option_id` |
| `social.checkin_created` | `checkin_id`, `person_id: opaque\|null` (enrolled + consented only), `label: string\|null` |
| `social.mood_updated` | `subject_id: opaque`, `mood: string` (from configured set) |
| `social.scoreboard_updated` | `board_id`, `entry_id`, `delta: int` |
| `social.deletion_requested` | `target_kind: "guestbook"\|"video_message"\|"photo"\|"checkin"\|"enrollment"`, `target_id` |

All public writes are rate-limited and produce moderation-capable records with timestamps.

## ambient.* — background integrations (control plane → wallboard)

| Type | Payload |
|---|---|
| `ambient.bird_summary` | `window: "today"`, `top_species: [{name, count, confidence_avg}]`, `total_detections: int` |
| `ambient.satellite_pass` | `satellite: string`, `rise_at: ISO-8601`, `max_elevation_deg: float`, `direction: string`, `visible: bool` |
| `ambient.aircraft_summary` | `nearby: [{callsign, altitude_ft, distance_km, heading}]`, `as_of: ISO-8601` |
| `ambient.printer_status` | `state: "idle"\|"printing"\|"paused"\|"error"\|"offline"`, `job_name: string\|null`, `progress_pct: float\|null`, `eta: ISO-8601\|null` |
| `ambient.food_recommendation` | `date: ISO-8601 date`, `title: string`, `detail: string\|null`, `provider: string` |

Wallboard consumes summaries only; raw integration data stays on the control plane.

## system.* — health and ops

| Type | Payload |
|---|---|
| `system.service_health` | `service: string`, `status: "ok"\|"degraded"\|"down"`, `detail: string\|null` |
| `system.storage_alert` | `host: string`, `mount: string`, `free_bytes: int`, `severity: "warning"\|"critical"` |
| `system.latency_sample` | `path: string` (from the budget table), `p50_ms: float`, `p95_ms: float`, `p99_ms: float`, `window_s: int` |

## Example instances

```json
{
  "event_id": "018f6b2e-7c9a-7000-8000-3f9d2ba41c11",
  "type": "door.button_pressed",
  "source": "esp32-door-controller",
  "occurred_at": "2026-07-04T12:34:56.123Z",
  "monotonic_ms": 123456789,
  "door_id": "primary",
  "trace_id": "9d1e8a34-52a1-4b0e-9c0f-2a7d1f66b100",
  "payload": { "press_id": "c0a8b1d2-0f0e-4a6b-b111-5a4e2f9b7788", "had_cached_profile": true, "profile_id": "blue_wave" }
}
```

```json
{
  "event_id": "018f6b2e-8d00-7000-8000-aa11bb22cc33",
  "type": "vision.identity_stable",
  "source": "door-visiond",
  "occurred_at": "2026-07-04T12:34:56.123Z",
  "monotonic_ms": 123456780,
  "door_id": "primary",
  "trace_id": "9d1e8a34-52a1-4b0e-9c0f-2a7d1f66b100",
  "payload": {
    "person_id": "prs_8f3k2j",
    "display_name": "Alex",
    "confidence": 0.93,
    "expires_at": "2026-07-04T12:34:58.623Z",
    "expires_at_monotonic_ms": 123459280,
    "profile_id": "blue_wave"
  }
}
```
