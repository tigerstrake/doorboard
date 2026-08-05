# door-visiond — real-time vision service

**Plane:** real-time door · **Host:** door Pi 5 · **Language:** Python · **Tasks:** T-302, T-303 (Codex), spec guarded by T-301 (Claude)

Sole owner of the Hailo accelerator and the recognition camera (ADR-0004). Detects faces, matches against the small enrolled set, maintains the short-lived `current_visitor` cache, and pushes profile updates to the ESP32.

## Pipeline (hardware target)

```text
recognition camera (libcamera) → face detect → landmarks/align → embed (Hailo)
  → match vs enrollment DB (SQLite on SSD)
  → stability filter: min face size, match in 2 of last 3 frames
  → current_visitor cache (TTL 2.5 s) → vision.identity_stable event
  → door.profile_update to ESP32 (profile_id + TTL only)
```

Greeting cooldown 30 s per person. Unknown faces: `vision.face_visible` only — no identity event, no persisted embedding, ever (ADR-0005).

The repository currently contains the complete privacy/matching pipeline and a
hardware adapter boundary, but not a verified Hailo camera/model implementation.
Hardware modes therefore fail closed to `disabled` unless runtime and model
metadata can both be verified. Hardware bring-up remains gated by issue #84;
the service must never claim `hailo_ok` while the placeholder adapter is active.

## Modes

`disabled | mock | single-camera | dual-camera | hardware` via `VISION_MODE`. Mock mode emits scripted identity events for dev/CI/simulator. Startup performs a Hailo runtime/model compatibility check; on failure it degrades to `disabled` (generic greetings) and reports via health — it never blocks the door.

## Interfaces

- Events out: `vision.face_visible`, `vision.identity_stable`, `vision.identity_expired`, `vision.privacy_mode_changed`, `vision.pipeline_status`; `door.profile_update`/`door.profile_clear` via Esp32Transport.
- HTTP: `GET /health`, `GET /metrics`, `GET /current-visitor`, `POST /enroll`, `POST /unenroll`, `POST /privacy-mode`, `POST|GET /invites`, `POST /invites/{id}/revoke`, `GET /relay-status`, `POST /relay-key/rotate` (everything except health/metrics/current-visitor is admin-authenticated).
- Reads: enrollment DB (SSD), pinned model files, config from `packages/config`.

## Remote enrollment (ADR-0016)

Enrollment works two ways. **At the door** is the default and unchanged: the admin UI captures from the Pi's own camera and nothing touches the internet. **From a phone** goes through a relay ([apps/enroll-web](../enroll-web/)) that only ever holds ciphertext:

```text
admin mints an invite  →  QR on the doorboard  →  phone seals photos + name to
this door's public key  →  relay stores ciphertext (15-min TTL)  →  this service
polls outbound, decrypts, embeds, wipes
```

The sealing keypair lives at `${enrollment_root}/relay/door_key.json` — on the encrypted enrollment volume, alongside the embedding database. Only the public half is ever published. The QR carries the key fingerprint in its URL fragment so a phone can detect a relay that substituted its own key.

Remote enrollment is **off unless configured**, and its failure modes never touch the door path:

| Variable | Meaning |
|---|---|
| `VISIOND_RELAY_BASE_URL` | Relay origin. Empty (default) → the poller never starts. |
| `VISIOND_RELAY_DEVICE_TOKEN` | Bearer token authenticating this Pi to the relay. |
| `VISIOND_RELAY_PUBLIC_URL` | Origin used to build invite URLs, if it differs from the API base. |
| `VISIOND_RELAY_POLL_INTERVAL_S` | Collection cadence (default 5 s). |
| `VISIOND_RELAY_INVITE_TTL_S` | How long a minted invite stays usable (default 1 h). |
| `VISIOND_RELAY_MAX_IMAGES` | Photos one invite may carry (default 5). |

An unreachable relay produces bounded backoff, `relay_status: "degraded"` in health, and nothing else — service status stays `ok`, because remote enrollment is a convenience and recognition is never authorization. Privacy mode or a locked enrollment volume stops collection entirely, so no plaintext is produced.

Outbound HTTPS to that one origin is the only internet egress this service has.

## Must never

Block button interaction; wait on the NUC; persist unknown embeddings; log biometric data; make or influence any access/security decision; share the Hailo device with another process.

## Key metrics

`inference_ms` (p50/p95), `face_to_identity_ms`, `cache_hit_rate`, `fps`, `frame_drops`, `hailo_ok`.
