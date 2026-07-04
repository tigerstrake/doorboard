# Doorboard Architecture

This is the operational summary of the system design. The full requirements document is [docs/handoff/doorboard_architecture_handoff.md](docs/handoff/doorboard_architecture_handoff.md); if this file and the handoff ever disagree, the handoff wins and an ADR must resolve the conflict. Binding decisions live in [docs/adr/](docs/adr/).

## 1. The two planes

The system is split into a **real-time door plane** and a **control plane**. This is the single most important structural decision (ADR-0002).

```text
REAL-TIME DOOR PLANE (hallway, medium/low trust, latency-critical)
┌─────────────────────────────────────────────────────────────┐
│  Door assembly (removable, hallway-facing)                   │
│                                                              │
│  ESP32-S3 ──UART──> Pi 5 (+ Hailo-8 26 TOPS AI HAT+, USB SSD)│
│   button              ├─ door-visiond   (owns Hailo, cameras)│
│   LED ring            ├─ door-media     (owns MediaMTX)      │
│   speaker             ├─ door-api       (session orchestr.)  │
│   knock sensor        ├─ door-ui        (kiosk SPA, 4 routes)│
│                       └─ door-sync      (async queue → NUC)  │
│                                                              │
│  HDMI×2: Wallboard (21.5–24" 1080p) + DoorPad (7" touch)     │
│  CSI×2: recognition cam (Std) + visitor cam (Wide NoIR)      │
└──────────────────────┬───────────────────────────────────────┘
                       │ single Ethernet + protected power loop
┌──────────────────────┴───────────────────────────────────────┐
│  CONTROL PLANE (inside room, trusted, latency-tolerant)      │
│                                                              │
│  NUC: control-plane-api, PostgreSQL, Mosquitto,              │
│       Home Assistant, wallboard-worker, Caddy                │
│  Pi 4: BirdNET-Go (isolated)                                 │
│  Pi 3/4 (optional): RTL-SDR / ADS-B                          │
│  NAS: durable archive only (never active DB, never realtime) │
└──────────────────────────────────────────────────────────────┘
```

**Critical path** (must work with NUC/NAS/internet down):
`physical button → ESP32 immediate response → cached profile if available → local UI transition`

**Non-critical path** (may lag, queue, retry):
`event log → NUC → database → notifications → NAS sync → analytics`

## 2. Trust model

| Component | Trust | Consequences |
|---|---|---|
| NAS | High | Durable archive; not broadly exposed; Pi uploads via limited service credential only |
| NUC | High | Only holder of admin secrets (HA admin, NAS admin, DB); config distribution point |
| Door Pi 5 | Medium/low | Physically stealable. No admin credentials, no long-term archive, per-device tokens, USB/microSD hidden in locked enclosure |
| ESP32 | Low | No secrets at all; profile cache holds only opaque profile IDs |
| Public screens | Low | Anyone can touch/see. Broad status only — never GPS, calendars, private notes, diagnostics |
| QR/PWA visitor endpoints | Low | Tokenized, rate-limited, short-lived |

## 3. Services

Each service has a full spec in its directory README. Every service exposes `GET /health` and `GET /metrics`, supports a simulation/mock mode, and uses typed events from `packages/contracts` — no ad hoc JSON (ADR-0003).

| Service | Host | Owns | Must never |
|---|---|---|---|
| [door-visiond](apps/door-visiond/) | Pi | Hailo device, recognition camera, identity cache, enrollment DB | Block button flow; wait on NUC; persist unknown embeddings; make access decisions |
| [door-media](apps/door-media/) | Pi | MediaMTX, recording lifecycle, thumbnails, retention | Wait synchronously on NAS in the visitor path |
| [door-api](apps/door-api/) | Pi | Visitor session state machine, button events, privacy mode, display broadcasts | Depend on control plane for session transitions |
| [door-ui](apps/door-ui/) | Pi | `/wallboard` `/doorpad` `/visitor` `/admin` `/diagnostics` kiosk SPA | Show sensitive data on public routes; require non-local APIs |
| [door-sync](apps/door-sync/) | Pi | Upload queue Pi→NUC/NAS, backoff, dedupe, checksum-verified cleanup | Delete local media before integrity verification |
| [control-plane-api](apps/control-plane-api/) | NUC | Event history, HA bridge, statuses, social features, integrations, notifications, config distribution | Sit in the door critical path |
| [wallboard-worker](apps/wallboard-worker/) | NUC | Scheduled jobs: satellite passes, aircraft polling, bird summaries, collages | Run on the door Pi |
| [simulator](apps/simulator/) | dev | Fake button/vision/camera/outage events for hardware-free development | — |
| [esp32-door-controller](firmware/esp32-door-controller/) | ESP32 | Button debounce, generic <30 ms feedback, profile cache, LED/audio, knock detection, watchdog fallback | Hold secrets; wait on anything before generic feedback |

## 4. Latency budgets (measured, not assumed)

| Path | Target |
|---|---:|
| Button → generic LED/audio (ESP32-local) | p95 < 30 ms |
| Button → cached personalized effect | p95 < 100 ms |
| Touchscreen tap → visible local response | p95 < 100 ms |
| Face visible → stable identity | p95 < 600 ms |
| Bell → visitor mode on large display | p95 < 250 ms |
| Bell → recording event (stream already live) | < 500 ms |
| Local live video (WebRTC) | < 750 ms |
| NAS upload | non-critical, after interaction |

The performance harness (M1/M7) makes these observable. A change that regresses a p95 target is a bug regardless of features added.

## 5. Identity cache (proactive recognition)

Recognition is proactive, never bell-triggered:

1. Recognition camera runs continuously; `door-visiond` requires a stable match (2 of last 3 frames, minimum face size).
2. On stability it writes a short-lived `current_visitor` cache (2.5 s TTL) and pushes a `door.profile_update` to the ESP32 (profile ID + monotonic expiry only).
3. Button press consumes the cache instantly; no cache means an immediate generic greeting.
4. Late recognition may update the display but never delays the initial interaction.
5. Greeting cooldown: 30 s per person. Unknown faces: generic greeting, nothing persisted.

## 6. Media pipeline

```text
visitor camera + mic → libcamera/rpicam capture → H.264/AAC → MediaMTX
   ├→ WebRTC (primary local live protocol; not HLS)
   ├→ both kiosk displays (one encode, many consumers)
   └→ segmented recording on USB SSD → finalized clip + thumbnail
         → door-sync queue → checksum-verified NAS upload → local cleanup
```

Camera streams **before** the bell press — no cold-start capture on press. Recording never blocks playback. Storage tiers are binding (ADR-0007): microSD = OS only, SSD = active data, NAS = archive.

## 7. Event system

All events use the shared envelope and catalog in [docs/protocols/events.md](docs/protocols/events.md), implemented once in `packages/contracts` (Pydantic v2 models + exported JSON Schema + generated TypeScript types). Transports:

- **UART** (preferred) Pi ↔ ESP32 for immediate profile/action messages ([wire protocol](docs/protocols/esp32-pi-protocol.md)); UDP acceptable; MQTT never the only immediate transport.
- **WebSockets** between Pi-local services and the kiosk displays.
- **MQTT (Mosquitto on NUC)** for control-plane fan-out, HA integration, and audit — never in the critical path.

Conventions: UTC internally, local timezone only at the display boundary; monotonic clocks for latency and expiry; opaque `person_id`s (never a name as a key); `trace_id` propagated end to end.

## 8. Visitor session state machine

Defined normatively in [docs/protocols/events.md §session](docs/protocols/events.md) and owned by `door-api`:

```text
IDLE → APPROACH_DETECTED → [IDENTITY_CACHED] → BUTTON_PRESSED → VISITOR_MODE
     → RINGING → ANSWERED | UNANSWERED_TIMEOUT → VIDEO_MESSAGE_OFFERED
     → VIDEO_MESSAGE_RECORDING → VIDEO_MESSAGE_REVIEW → VIDEO_MESSAGE_SAVED → SESSION_END
```

Button press enters visitor mode immediately; generic feedback precedes any network operation; sessions survive browser reloads and auto-expire; admin actions are separate and authenticated.

## 9. Privacy invariants (non-negotiable)

- Face matching only for a manually enrolled, explicitly consenting list; personalization only, **never** authorization.
- Unknown faces are never named; unknown embeddings are never persisted.
- Video messages are always visitor-initiated; visible camera notice text on the assembly.
- Privacy mode disables recognition while preserving basic door interaction.
- Deletion flows exist for messages, guestbook entries, photos, and enrollments.
- Presence uses broad labels only (Available/Busy/DND/Sleeping/At Class/At Library/Away/Unknown); manual override outranks inference; no raw GPS anywhere.
- Never log raw biometric data.

Any PR touching enrollment, embeddings, retention, or public display content requires `agent:claude` review before merge (ADR-0005, ADR-0008).

## 10. Failure behavior

| Failure | Required behavior |
|---|---|
| NUC offline | Pi UI + local recording continue; events queue in SQLite |
| NAS offline | clips stay on SSD; door-sync retries with backoff |
| Hailo unavailable | generic greeting; zero personalized behavior |
| Camera unavailable | button/UI still work; UI shows "video unavailable" |
| ESP32 offline | Pi surfaces admin error; never pretend a physical effect occurred |
| Pi restart | ESP32 runs generic fallback animation / unavailable state |
| Internet offline | no impact on core flow |
| Storage low | stop recording safely, preserve interaction, alert control plane |

## 11. Technology stack (ADR-0003)

- **Backend:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy + Alembic, pytest, Ruff, Pyright, OpenTelemetry-compatible structured JSON logs. Workspace via `uv`.
- **Frontend:** TypeScript, React + Vite, WebSocket event client, shared `ui-kit`, Chromium kiosk. Workspace via `pnpm`.
- **Data:** SQLite (WAL) on Pi; PostgreSQL on NUC; NAS archive-only.
- **Media:** MediaMTX, `rpicam-apps`/libcamera, H.264, WebRTC.
- **Vision:** Hailo runtime behind a replaceable interface in `door-visiond`; modes `disabled | mock | single-camera | dual-camera | hardware`; pinned runtime/model versions; the official Pi/Hailo face-recogniser is a reference, not a dependency to build on directly.
- **Deploy:** systemd for Pi hardware-facing services; Docker Compose on NUC; offline boot required.

## 12. What is deliberately excluded

No door unlocking or access control. No recognition of non-consenting people. No cloud face APIs. No continuous surveillance recording. No two-way intercom in v1. No exact-location display. No monolith. No heavy background jobs on the door Pi (BirdNET, Immich, full HA, ADS-B all live elsewhere).
