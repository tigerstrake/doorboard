# Monitoring Dashboards Reference

**Status:** Rewritten 2026-09-25 from the source. The previous version listed six `esp32_link_*` metrics that no service has ever emitted and an alert table missing three rules; `tests/test_monitoring_rules.py` now fails if a name here, in `alert.rules.yml` or in the dashboard is not emitted by some exporter under `apps/`.
**Verified-on:** simulator (mock mode), 2026-07-08 for the endpoints; the Pi scrape path (through the LAN proxy) and the node-exporter thermal label are not yet verified on the door.

This is the reference of the health endpoints, Prometheus metrics and log event names the Doorboard services emit, and what the NUC's stack (`infra/monitoring/`) does with them. The Grafana dashboard is `infra/monitoring/grafana/dashboards/doorboard.json`.

## 0. How the door is scraped

Every door service binds `127.0.0.1`. Prometheus on the NUC reaches them through the Pi's LAN proxy (ADR-0047): job `door-api` scrapes `door-pi.local:80` with `metrics_path: /metrics/door-api`, and so on for `door-visiond`, `door-media`, `door-sync`, `door-voice` and `node` (prometheus-node-exporter on the Pi, loopback, `/metrics/node`). `door-pi.local` must resolve inside the Prometheus container — see `infra/monitoring/README.md`. On the Pi itself, `curl 127.0.0.1:<port>/metrics` is the same data.

---

## 1. Metrics and Health Endpoints by Service

### A. door-api (Door Pi, `127.0.0.1:8080`)
* **Health:** `GET /health` → `{"service": "door-api", "status": "ok" | "degraded", "detail": null | "dead-lettered outbox events: media=N sync=N; …"}`. `degraded` means a mirror the NUC will never receive without operator action; the door itself is fine.
* **Metrics (`GET /metrics`):**
  * Session machine: `session_transitions_total`, `session_illegal_transitions_total`, `session_sessions_started_total`, `session_sessions_ended_total`, `session_timer_fires_total`.
  * Social: `social_guestbook_created_total`, `social_guestbook_rejected_invalid_total`, `social_guestbook_rejected_rate_limited_total`, `social_poll_votes_cast_total`, `social_poll_votes_rejected_total`, `social_checkins_created_total`, `social_checkins_rejected_total`, `social_deletion_requests_total`, `social_moderation_log_pruned_total`; `door_api_social_moderation_log_rows` (gauge).
  * DoorPad effects: `door_api_doorpad_effect_requests_total`, `door_api_doorpad_effect_unavailable_total`.
  * ESP32 link (ADR-0040 — door-api owns the UART): `door_api_esp32_link_enabled` (gauge, 0 = no controller configured), `door_api_esp32_transport_attached`, `door_api_esp32_link_connected` (gauge 1/0), `door_api_esp32_link_connects_total`, `door_api_esp32_link_reopens_total`, `door_api_esp32_link_open_failures_total`, `door_api_esp32_link_idle_timeouts_total`; profile relay from visiond: `door_api_esp32_profile_relayed_total`, `door_api_esp32_profile_relay_failed_total`, `door_api_esp32_profile_relay_noop_total`.
  * Outboxes to the NUC: `door_api_media_outbox_depth` (gauge), `door_api_media_outbox_dropped_total`, `door_api_media_outbox_dead_total`, `door_api_media_forward_successes_total`, `door_api_media_forward_errors_total`; the same five with `sync_` in place of `media_`.
  * MQTT bridge (NUC ambient/presence → `/ws`): `door_api_mqtt_bridge_enabled`, `door_api_mqtt_bridge_messages_received_total`, `door_api_mqtt_bridge_messages_broadcast_total`, `door_api_mqtt_bridge_parse_errors_total`, `door_api_mqtt_bridge_broadcast_errors_total`.

### B. door-visiond (Door Pi, `127.0.0.1:8081`)
* **Health:** `GET /health` → `status` (`ok` | `degraded`), `mode` / `configured_mode`, `hailo_ok`, `privacy_enabled`, `enrolled`, `enrollment_locked` (the LUKS volume is not open — recognition off, door unaffected), `compat`, `face_frame_source`, `runtime_warning`, `esp32_connected`, `esp32_profile_push_status` (`ok` | `degraded`) + `esp32_profile_warning`, `privacy_state_status`, `self_enrolled`, `snapshot_duplicate_frames` (climbing = door-media's snapshot reader stalled), `archive_purge_queue_depth`, `event_forwarding` (`ok` | `failing` | `disabled` — the screen half of the greeting; not part of `status`) + `event_forward_last_error`, `relay_status` / `relay_configured` (not part of `status`).
* **Metrics (`GET /metrics`):** every key of the service's snapshot as `door_visiond_<key>` (all gauges): `frame_count`, `face_visible_count`, `identity_stable_count`, `identity_expired_count`, `frame_drops`, `fps`, `inference_ms_p50` / `inference_ms_p95`, `face_to_identity_ms_p50` / `face_to_identity_ms_p95` (budget < 600 ms, ARCHITECTURE §4), `snapshot_duplicate_frames`, `self_enrolled`, `cache_hit_rate`, `enrolled`, `esp32_profile_updates_acked`, `esp32_profile_clears_acked`, `esp32_profile_send_failures`, `archive_purge_queue_depth`, `archive_purges_delivered`, `archive_purges_failed`, `pipeline_errors`, `backend_degradations`, `backend_recoveries`, `events_forwarded`, `events_forward_dropped`, `events_forward_failures`, `relay_enabled`, `relay_polls_ok`, `relay_polls_failed`, `relay_polls_skipped_idle`, `relay_bundles_enrolled`, `relay_bundles_rejected`, `relay_consecutive_failures` and the remaining `relay_*` counters; plus `door_visiond_uptime_s` (counter).

### C. door-media (Door Pi, `127.0.0.1:8082`)
* **Health:** `GET /health` → `{"service": "door-media", "status": "ok" | "degraded", "detail": null | "MediaMTX stream not live", "mode", "stream_up", "door_id"}`.
* **Metrics:** `door_media_stream_up` (gauge 1/0), `door_media_webrtc_clients`, `door_media_ssd_free_bytes`, `door_media_sync_queue_depth`, `door_media_oldest_unsynced_s`, `door_media_uptime_s` (counter).

### D. door-sync (Door Pi, `127.0.0.1:8083`)
* **Health:** `GET /health` → `{"service": "door-sync", "status": "ok" | "degraded", "detail": "N dead-lettered; oldest pending item is Ns old (…)", "door_id"}` — degraded on dead-letters **or** on a backlog older than `pending_age_degraded_s` (an unreachable NAS never dead-letters, so age is the signal).
* **Metrics:** `door_sync_queue_depth`, `door_sync_oldest_pending_s`, `door_sync_dead_letter_total`, `door_sync_completed_total`, `door_sync_queue_items{target="nuc"|"nas", status="pending"|"completed"|"dead_letter"}`, `door_sync_uptime_s` (counter).

### E. door-voice (Door Pi, `127.0.0.1:8084`)
* Spoken greeting (ADR-0034). Its loopback `/health` + `/metrics` are being added alongside this rewrite; the health agent probes `/health` only while the unit is active, and the proxy maps `/metrics/door-voice`. Fill in the metric names here when they land.

### F. control-plane-api (NUC, `control-plane-api:8090`)
* **Health:** `GET /health` → `{"service": "control-plane-api", "status": "ok", "detail": null, "door_id"}`.
* **Metrics:** `control_plane_api_events_total` (counter), `control_plane_api_uptime_s` (counter).

### G. Host (Door Pi, node exporter behind `/metrics/node`)
* `node_thermal_zone_temp{type="cpu-thermal"}` for the thermal panel and `PiThrottling`; the collectors enabled are cpu, meminfo, filesystem, loadavg, thermal_zone, hwmon, diskstats, uname, time.

---

## 2. Alert Rules (`infra/monitoring/alert.rules.yml`)

| Alert | Expression | For | Meaning |
|---|---|---|---|
| **ServiceDown** | `up == 0` | 1 m | A scrape target is unreachable — for the door jobs, the proxy or the service behind it. |
| **StorageLow** | `door_media_ssd_free_bytes < 10737418240` | 5 m | Under 10 GiB free on the SSD; door-media stops recording safely at its own floor ([storage-full.md](storage-full.md)). |
| **SyncQueueAging** | `(door_media_oldest_unsynced_s > 14400) or (door_sync_oldest_pending_s > 14400)` | 5 m | Something has waited > 4 h to reach the NUC/NAS. |
| **ESP32Offline** | `door_api_esp32_link_enabled == 1 and door_api_esp32_link_connected == 0` | 1 m | A configured controller link is down; the bell runs on the ESP32's own fallback. |
| **PiThrottling** | `node_thermal_zone_temp{type="cpu-thermal"} > 80` | 2 m | SoC over 80 °C. |
| **MediaStreamDown** | `door_media_stream_up == 0` | 2 m | Black/frozen camera; recordings and the recognition snapshot are dead. |
| **SyncDeadLetter** | `door_sync_dead_letter_total > 0` | 5 m | Items that will never upload without operator action. |
| **OutboxDeadLetter** | `(door_api_media_outbox_dead_total > 0) or (door_api_sync_outbox_dead_total > 0)` | 5 m | A door-api mirror event parked after its retry cap. |
| **AlertmanagerReceiverFailing** | `increase(alertmanager_notifications_failed_total[10m]) > 0` | 10 m | The one hop that fails invisibly: the webhook to control-plane-api `/admin/alerts`. By construction this alert cannot be delivered through the receiver it reports on — read it on the Prometheus alerts page. |

Alertmanager's single receiver posts to control-plane-api `POST /admin/alerts` with the NUC admin bearer; the control plane fans out to the owner via ntfy/Telegram. Backups have no metric: the backup-stale check is the compose healthcheck on `postgres-backup` ([backup-procedures.md](backup-procedures.md)).

---

## 3. Log Event Names (Packages/Contracts Event Ingest)

Structured logging and MQTT fan-out use the following event tags from the `packages/contracts` catalog (`docs/protocols/events.md` is normative):

* `door.button_pressed` — Emitted when the hallway physical button is depressed.
* `vision.identity_stable` — Emitted by `door-visiond` when a visitor's face is stably matched.
* `vision.identity_expired` — Emitted when a matched visitor leaves the camera view.
* `media.segment_finalized` — Emitted when a recording segment is fully written to the SSD.
* `media.thumbnail_ready` — The still the NUC's ring-photo delivery waits for (ADR-0022).
* `sync.upload_queued` — Logged when `door-sync` enqueues a database event or media asset.
* `sync.upload_completed` — Logged when an item is successfully transferred to the NUC/NAS.
* `sync.upload_failed` — Logged when a transfer attempt fails.
