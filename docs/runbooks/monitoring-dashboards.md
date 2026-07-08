# Monitoring Dashboards Reference

**Status:** Verified
**Walkthrough Date:** 2026-07-08 (Simulated/mock mode verified; hardware-specific steps marked and deferred)

This document provides a reference of the actual Prometheus metrics, health endpoints, and structured log event names emitted by the Doorboard services. These are plotted on the Grafana dashboard (`infra/monitoring/grafana/dashboards/doorboard.json`).

---

## 1. Metrics and Health Endpoints by Service

### A. door-api (Door Pi)
* **Health Endpoint:** `GET /health`
  * Response: `{"status": "ok"}`
* **Prometheus Metrics (`GET /metrics`):**
  * `session_transitions_total` (counter) — Total session transitions processed.
  * `session_illegal_transitions_total` (counter) — Total illegal transition attempts.
  * `session_sessions_started_total` (counter) — Total visitor sessions initialized.
  * `session_sessions_ended_total` (counter) — Total visitor sessions closed.
  * `session_timer_fires_total` (counter) — Total session timeout events.
  * `social_guestbook_created_total` (counter) — Guestbook entry creations.
  * `social_guestbook_rejected_invalid_total` (counter) — Rejected invalid guestbook submissions.
  * `social_guestbook_rejected_rate_limited_total` (counter) — Rejected rate-limited guestbook submissions.
  * `social_poll_votes_cast_total` (counter) — Total cast votes.
  * `social_poll_votes_rejected_total` (counter) — Total rejected votes.
  * `social_checkins_created_total` (counter) — Total check-ins created.
  * `social_checkins_rejected_total` (counter) — Total rejected check-ins.
  * `social_deletion_requests_total` (counter) — Total privacy deletion requests processed.
  * `door_api_doorpad_effect_requests_total` (counter) — Total personal profile sound/light effect requests.
  * `door_api_doorpad_effect_unavailable_total` (counter) — Total requested effects that failed/were unavailable.
  * `door_api_media_forward_errors_total` (counter) — Errors forwarding media notifications.
  * `esp32_link_connected` (gauge: 1=connected, 0=disconnected) — ESP32 UART link status.
  * `esp32_link_last_heartbeat_mono_ms` (gauge) — Monotonic timestamp of the last ESP32 heartbeat.
  * `esp32_link_rx_errors_total` (counter) — Checksum/framing errors on serial read.
  * `esp32_link_tx_retries_total` (counter) — Transmit retries to the ESP32.
  * `esp32_link_tx_timeouts_total` (counter) — Transmit timeouts on the serial line.
  * `esp32_link_duplicate_rx_total` (counter) — Deduplicated packet counts.

---

### B. door-visiond (Door Pi)
* **Health Endpoint:** `GET /health`
  * Response fields:
    * `status`: `"ok"` or `"degraded"`
    * `mode`: Active vision mode (e.g. `mock` or `hardware`)
    * `hailo_ok`: boolean (whether Hailo-8 chip is active and communicating)
    * `privacy_enabled`: boolean
    * `enrolled`: count of enrolled face profiles
    * `esp32_connected`: boolean
    * `esp32_profile_push_status`: `"ok"` or `"degraded"`
    * `esp32_profile_warning`: error details if push failed
* **Prometheus Metrics (`GET /metrics`):**
  * `door_visiond_frame_count` (gauge) — Number of frames ingested from CSI camera.
  * `door_visiond_face_visible_count` (gauge) — Number of frames with visible faces.
  * `door_visiond_identity_stable_count` (gauge) — Stable face matches identified.
  * `door_visiond_identity_expired_count` (gauge) — Face detection expirations.
  * `door_visiond_frame_drops` (gauge) — CSI camera dropped frame counts.
  * `door_visiond_fps` (gauge) — Current pipeline ingestion FPS.
  * `door_visiond_inference_ms_p50` (gauge) — Median Hailo inference latency in milliseconds.
  * `door_visiond_inference_ms_p95` (gauge) — p95 Hailo inference latency.
  * `door_visiond_face_to_identity_ms_p50` (gauge) — Median face matching processing latency.
  * `door_visiond_face_to_identity_ms_p95` (gauge) — p95 face matching processing latency (Budget: <600 ms).
  * `door_visiond_cache_hit_rate` (gauge) — Identity cache lookup hits.
  * `door_visiond_enrolled` (gauge) — Current database size of enrolled profiles.
  * `door_visiond_esp32_profile_updates_acked` (gauge) — Successful profile updates pushed to ESP32.
  * `door_visiond_esp32_profile_clears_acked` (gauge) — Successful profile clears pushed to ESP32.
  * `door_visiond_esp32_profile_send_failures` (gauge) — Failed UART pushes to ESP32.
  * `door_visiond_uptime_s` (counter) — Uptime of the vision daemon.

---

### C. door-media (Door Pi)
* **Health Endpoint:** `GET /health`
  * Response: `{"service": "door-media", "status": "ok"|"degraded", "detail": ..., "mode": ..., "stream_up": true|false, "door_id": ...}`
* **Prometheus Metrics (`GET /metrics`):**
  * `door_media_stream_up` (gauge: 1=up, 0=down) — MediaMTX capture stream status.
  * `door_media_webrtc_clients` (gauge) — Active WebRTC consumers connected.
  * `door_media_ssd_free_bytes` (gauge) — Free space remaining on the USB SSD mount.
  * `door_media_sync_queue_depth` (gauge) — Finalized recording segments queued for sync.
  * `door_media_oldest_unsynced_s` (gauge) — Age of the oldest unsynced recording in queue.
  * `door_media_uptime_s` (counter) — Service uptime in seconds.

---

### D. door-sync (Door Pi)
* **Health Endpoint:** `GET /health`
  * Response: `{"service": "door-sync", "status": "ok"|"degraded", "detail": "N dead-lettered", "door_id": ...}`
* **Prometheus Metrics (`GET /metrics`):**
  * `door_sync_queue_depth` (gauge) — Items in the SQLite transfer queue awaiting upload.
  * `door_sync_oldest_pending_s` (gauge) — Age of the oldest pending queue item in seconds.
  * `door_sync_dead_letter_total` (gauge) — Total items moved to the dead-letter queue (requires admin intervention).
  * `door_sync_completed_total` (gauge) — Items uploaded successfully, awaiting pruning.
  * `door_sync_queue_items{target="...", status="..."}` (gauge) — Breakdown of queue items by destination (`nuc` / `nas`) and state (`pending` / `completed` / `dead_letter`).
  * `door_sync_uptime_s` (counter) — Service uptime in seconds.

---

### E. control-plane-api (NUC)
* **Health Endpoint:** `GET /health`
  * Response: `{"service": "control-plane-api", "status": "ok", "detail": null, "door_id": ...}`
* **Prometheus Metrics (`GET /metrics`):**
  * `control_plane_api_events_total` (counter) — Total events stored durably in the Postgres DB.
  * `control_plane_api_uptime_s` (counter) — Service uptime in seconds.

---

## 2. Key Alert Rules to Watch

The alerting rules configured in `infra/monitoring/alert.rules.yml` target the following critical failure scenarios:

| Alert Name | Expression | Warning Threshold | Description |
|---|---|---|---|
| **ServiceDown** | `up == 0` | 1 minute | Triggers if any scraped target fails to respond. |
| **StorageLow** | `door_media_ssd_free_bytes < 10737418240` | 5 minutes | Fires if free space on the USB SSD drops below 10 GiB. |
| **SyncQueueAging** | `(door_media_oldest_unsynced_s > 14400) or (door_sync_oldest_pending_s > 14400)` | 5 minutes | Fires if the sync backlog contains items older than 4 hours. |
| **ESP32Offline** | `esp32_link_connected == 0` | 1 minute | Triggers if the Pi loses communication with the physical button controller. |
| **PiThrottling** | `node_hwmon_temp_celsius{chip="platform_soc_thermal"} > 80` | 2 minutes | Warning that Pi CPU is reaching thermal limits. |

---

## 3. Log Event Names (Packages/Contracts Event Ingest)

Structured logging and MQTT fan-out use the following event tags from the `packages/contracts` catalog:

* `door.button_pressed` — Emitted when the hallway physical button is depressed.
* `vision.identity_stable` — Emitted by `door-visiond` when a visitor's face is stably matched.
* `vision.identity_expired` — Emitted when a matched visitor leaves the camera view.
* `media.segment_finalized` — Emitted when a recording segment is fully written to the SSD.
* `sync.upload_queued` — Logged when `door-sync` enqueues a database event or media asset.
* `sync.upload_completed` — Logged when an item is successfully transferred to the NUC/NAS.
* `sync.upload_failed` — Logged when a transfer attempt fails.
