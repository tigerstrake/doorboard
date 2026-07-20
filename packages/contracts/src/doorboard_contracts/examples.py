from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from doorboard_contracts.events import EVENT_TYPE_TO_MODEL

EVENT_ID = UUID("018f6b2e-7c9a-7000-8000-3f9d2ba41c11")
TRACE_ID = UUID("9d1e8a34-52a1-4b0e-9c0f-2a7d1f66b100")
SESSION_ID = UUID("0f7e3f6f-6d69-44f7-b2e0-20f520cb04f5")
RECORDING_ID = UUID("c0a8b1d2-0f0e-4a6b-b111-5a4e2f9b7788")
ITEM_ID = UUID("77f7066e-a7c0-4de6-a670-8d432d4122c7")
ENTRY_ID = UUID("9f3a02d5-6442-4478-a5bb-0b84f5e915db")

OCCURRED_AT = datetime(2026, 7, 4, 12, 34, 56, 123000, tzinfo=UTC)
EXPIRES_AT = datetime(2026, 7, 4, 12, 34, 58, 623000, tzinfo=UTC)
NEXT_RETRY_AT = datetime(2026, 7, 4, 12, 39, 56, 123000, tzinfo=UTC)


PAYLOADS: dict[str, dict[str, Any]] = {
    "door.button_pressed": {
        "press_id": RECORDING_ID,
        "had_cached_profile": True,
        "profile_id": "blue_wave",
    },
    "door.knock_detected": {"pattern_id": "shave-and-a-haircut", "confidence": 0.91},
    "door.contact_changed": {"state": "closed"},
    "door.profile_update": {
        "profile_id": "blue_wave",
        "expires_at_monotonic_ms": 123459280,
        "priority": "normal",
    },
    "door.profile_clear": {"reason": "expired"},
    "door.effect_play": {"effect_id": "generic_chime", "duration_ms": 1500},
    "door.controller_health": {
        "uptime_s": 3600,
        "fw_version": "0.1.0",
        "cached_profile_id": "blue_wave",
        "fallback_active": False,
    },
    "vision.face_visible": {"face_count": 1, "largest_face_px": 184},
    "vision.identity_stable": {
        "person_id": "prs_8f3k2j",
        "display_name": "Alex",
        "confidence": 0.93,
        "expires_at": EXPIRES_AT,
        "expires_at_monotonic_ms": 123459280,
        "profile_id": "blue_wave",
    },
    "vision.identity_expired": {"person_id": "prs_8f3k2j"},
    "vision.privacy_mode_changed": {"enabled": True, "changed_by": "admin"},
    "vision.pipeline_status": {
        "mode": "mock",
        "hailo_ok": False,
        "fps": 15.0,
        "inference_ms_p50": 22.5,
    },
    "session.state_changed": {
        "session_id": SESSION_ID,
        "from_state": "BUTTON_PRESSED",
        "to_state": "VISITOR_MODE",
        "trigger": "button",
    },
    "session.started": {"session_id": SESSION_ID, "entry": "button"},
    "session.ended": {"session_id": SESSION_ID, "outcome": "message_left"},
    "media.recording_started": {
        "recording_id": RECORDING_ID,
        "session_id": SESSION_ID,
        "kind": "bell_clip",
        "stream": "visitor-main",
    },
    "media.recording_finalized": {
        "recording_id": RECORDING_ID,
        "path": "recordings/2026/07/04/bell.mp4",
        "duration_s": 12.4,
        "size_bytes": 1048576,
        "sha256": "0" * 64,
        "consent_context": "bell_event",
    },
    "media.thumbnail_ready": {
        "recording_id": RECORDING_ID,
        "path": "thumbnails/2026/07/04/bell.jpg",
    },
    "media.retention_deleted": {"recording_id": RECORDING_ID, "reason": "synced"},
    "media.storage_status": {
        "free_bytes": 5000000000,
        "queue_depth": 2,
        "oldest_unsynced_s": 120,
        "recording_allowed": True,
    },
    "sync.upload_queued": {
        "item_id": ITEM_ID,
        "recording_id": RECORDING_ID,
        "target": "nas",
    },
    "sync.upload_completed": {
        "item_id": ITEM_ID,
        "verified_sha256": "0" * 64,
        "attempts": 1,
    },
    "sync.upload_failed": {
        "item_id": ITEM_ID,
        "attempts": 2,
        "next_retry_at": NEXT_RETRY_AT,
        "error_class": "TimeoutError",
    },
    "status.presence_changed": {
        "subject_id": "owner",
        "label": "available",
        "source": "manual",
        "until": None,
    },
    "social.guestbook_entry_created": {
        "entry_id": ENTRY_ID,
        "text": "Hello from the hallway.",
        "author_label": "Taylor",
    },
    "social.poll_vote_cast": {"poll_id": "snack_poll", "option_id": "tea"},
    "social.checkin_created": {
        "checkin_id": ENTRY_ID,
        "person_id": "prs_8f3k2j",
        "label": "Alex",
        "photo_recording_id": str(RECORDING_ID),
    },
    "social.mood_updated": {"subject_id": "owner", "mood": "focused"},
    "social.scoreboard_updated": {"board_id": "daily", "entry_id": ENTRY_ID, "delta": 1},
    "social.deletion_requested": {"target_kind": "guestbook", "target_id": str(ENTRY_ID)},
    "ambient.bird_summary": {
        "window": "today",
        "top_species": [{"name": "House Finch", "count": 4, "confidence_avg": 0.88}],
        "total_detections": 7,
    },
    "ambient.satellite_pass": {
        "satellite": "ISS",
        "rise_at": NEXT_RETRY_AT,
        "max_elevation_deg": 64.5,
        "direction": "NW",
        "visible": True,
    },
    "ambient.aircraft_summary": {
        "nearby": [
            {
                "callsign": "UAL123",
                "altitude_ft": 32000,
                "distance_km": 18.2,
                "heading": 94,
            }
        ],
        "as_of": OCCURRED_AT,
    },
    "ambient.printer_status": {
        "state": "printing",
        "job_name": "door-bracket",
        "progress_pct": 42.0,
        "eta": NEXT_RETRY_AT,
    },
    "ambient.food_recommendation": {
        "date": date(2026, 7, 4),
        "title": "Noodle soup",
        "detail": "Good between classes.",
        "provider": "manual",
    },
    "system.service_health": {
        "service": "door-api",
        "status": "ok",
        "detail": None,
    },
    "system.storage_alert": {
        "host": "door-pi",
        "mount": "/mnt/door-ssd",
        "free_bytes": 400000000,
        "severity": "warning",
    },
    "system.latency_sample": {
        "path": "Button to generic LED/audio",
        "p50_ms": 12.0,
        "p95_ms": 24.0,
        "p99_ms": 29.0,
        "window_s": 300,
    },
}


def example_event(event_type: str) -> Any:
    model: Any = EVENT_TYPE_TO_MODEL[event_type]
    return model(
        event_id=EVENT_ID,
        type=event_type,
        source="doorboard-contracts-test",
        occurred_at=OCCURRED_AT,
        monotonic_ms=123456789,
        door_id="primary",
        trace_id=TRACE_ID,
        payload=PAYLOADS[event_type],
    )


def example_events() -> list[Any]:
    return [example_event(event_type) for event_type in EVENT_TYPE_TO_MODEL]
