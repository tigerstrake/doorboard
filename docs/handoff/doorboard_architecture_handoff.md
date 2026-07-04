# Doorboard: Open-Source Smart Door and Smart Room System
## Architecture and Software Development Handoff for Codex

**Status:** Architecture-definition document  
**Target deployment:** Indoor hallway-facing, removable, door-mounted system  
**Project principle:** Local-first, open-source, modular, and responsive  
**Primary user:** One room occupant, optional roommate, consenting friends/family  
**Audience:** An engineering/coding agent with no prior project context

---

# 1. Executive summary

Build an open-source, self-hosted smart door and smart-room system centered on a removable door-mounted hardware assembly. The system is partly practical and partly social: it provides a responsive DIY doorbell/video-message experience, opt-in face-recognition greetings, a large public “Doorboard” information display, a smaller touchscreen control surface, status indicators, voluntary guestbook/poll/check-in features, and integrations for birds, aircraft, satellites, a 3D printer, roommate status, mood, scoreboard, and food recommendations.

The most important engineering rule is:

> Any interaction that occurs at the physical door must react immediately and must not depend on the NUC, NAS, cloud services, Home Assistant, background jobs, or a fresh AI inference request.

The architecture is split into:

1. **Door Appliance, Raspberry Pi 5:** A physically door-mounted, latency-critical appliance that owns camera capture, accelerated face recognition, local video buffering/recording, the large display, the small touchscreen, the visitor UI, and communication with an ESP32 real-time controller.
2. **Door I/O Controller, ESP32-S3:** A real-time physical controller for button input, LED output, audio feedback, optional knock sensing, and fallback behavior.
3. **Indoor NUC:** Trusted control-plane host. It runs Home Assistant, longer-lived databases, dashboards, integrations, APIs, syncing, background tasks, and noncritical business logic.
4. **Spare Pi 4:** Dedicated BirdNET-Go node near a window.
5. **Spare Pi 3 or Pi 4, optional:** ADS-B/RTL-SDR aircraft receiver or another isolated peripheral workload.
6. **NAS:** Durable archive for completed clips, photos, long-term media, and backups.

The Pi door appliance should keep doing the following if the NUC or NAS is unavailable:

- show the local UI;
- react to the physical button;
- use a cached recognized identity;
- record a short visitor clip to its SSD;
- provide a locally connected display experience;
- retry synchronization later.

The door appliance is deliberately not the trusted core of the system. It is physically accessible from the hallway. It must not store master credentials, NAS administrator credentials, complete long-term photo archives, or unrestricted Home Assistant secrets.

---

# 2. Scope and intended experience

## 2.1 What this system is trying to achieve

### A. Door and visitor experience

- Physical DIY doorbell button.
- Immediate visual and audio feedback after press.
- A small touchscreen where a visitor can ring, leave a video message, leave a guestbook note, vote, voluntarily check in, or trigger playful interactions.
- A large public display that changes between an ambient dashboard and visitor-facing mode.
- Camera and microphone for voluntary visitor video messages.
- Opt-in recognition of a small list of consenting people, providing personalized greeting colors and/or sounds.
- Voluntary visitor log and playful “most frequent visitor” statistic.
- Hidden/easter-egg interaction, preferably a door vibration/knock pattern recognized locally by ESP32 hardware rather than hallway-audio surveillance.
- Physical/digital guestbook and poll/voting board.

### B. Personal presence and room status

- Visible broad status: Available, Busy, Do Not Disturb, Sleeping, At Class, At Library, Away, or Unknown.
- Weasley-clock-style status for owner and roommate.
- Source status from opt-in phone shortcuts, calendar-aware inference, and manual override.
- No raw GPS history required or desired. Use broad labels only.
- Manual override always outranks inferred calendar/location state.

### C. Ambient and informational dashboard

- Bird detections near the window. Processing delay is acceptable.
- Nearby aircraft information.
- Visible satellite passes, including ISS.
- 3D printer status and optional low-latency camera preview.
- Mood check-in.
- Casual roommate score/bet board.
- Daily food recommendation from an existing personal preference system.
- Photo booth workflow and private sharing gallery.

### D. Technical goals

- Entirely self-hosted/local-first for core functionality.
- No cloud dependency for door response.
- Open-source project structure.
- Modular services that can be independently started, stopped, simulated, or replaced.
- Reliable local event system.
- Full observability and testability.
- Attractive public display experience.
- Removable hardware installation at the end of the school year.

## 2.2 Non-goals

- Security access control, unlocking doors, or safety-critical authorization.
- Face recognition of non-consenting people.
- Passive tracking of everyone who passes the door.
- Cloud-first camera service.
- Continuous long-term surveillance recording.
- Full two-way intercom in version one.
- Facial recognition as the only trigger for a door action.
- Exact location display to the public.
- Exterior weatherproof design.
- A monolithic application where every feature runs on one Raspberry Pi.

## 2.3 Privacy requirements

- Face matching is only for a manually enrolled, explicitly consenting list.
- Unknown faces must not be named.
- Unknown facial embeddings must not be persisted.
- Visitor logs must be voluntary check-ins or events involving enrolled individuals.
- Video messages must be intentionally initiated by the visitor through the touchscreen or explicit button flow.
- Public screens may show broad status only, never exact GPS, complete calendars, private notes, or sensitive data.
- Support a deletion flow for visitor messages, guestbook entries, photos, and face enrollment.
- Support a privacy mode that disables face recognition while preserving basic door interaction.
- Include visible camera/video-message notice text.

---

# 3. Physical deployment assumptions

## 3.1 Door-mounted system

The main assembly is mounted on the hallway-facing side of a door. It is indoors and removable. No drilling into the wall or door is assumed.

Mechanical concept: removable “door backpack” / over-door suspended frame:

- Two padded over-door hooks at the top edge.
- Rigid lightweight front frame, ideally 2020/2040 aluminum extrusion plus aluminum composite, thin plywood, or printed panel.
- Large monitor mounted by VESA plate.
- Small touchscreen below.
- Cameras, physical button, speaker, LED ring, and microphone on front.
- Pi 5, AI HAT+, SSD, ESP32, and power distribution in a lockable ventilated enclosure behind the front panel.
- Inside-door load-spreader/tension bar and cam-buckle stabilization straps.
- Soft felt/EVA pads wherever frame contacts door.
- Flexible low-strain cable loop near hinge side for Ethernet and power from inside room.

The system should be structurally tested with dead weight before electronics are mounted.

## 3.2 Physical security model

The Pi is physically accessible from hallway. Assume it can be unplugged, rebooted, or stolen. Therefore:

- No NAS admin credentials on Pi.
- No full Home Assistant admin credentials on Pi.
- No long-term archive on Pi.
- Use limited service credentials for uploads.
- Prefer per-device API tokens or mutual TLS if practical.
- Hide USB and microSD access inside locked enclosure.
- Use watchdog and automatic restart.
- Provide safe degraded mode when NUC is unreachable.

## 3.3 Cable and power assumptions

Aim to route only:

- one Ethernet cable;
- one protected power cable or low-voltage DC line;

from inside room to door assembly.

All camera, display, button, LED, and audio wiring should be short and local to door frame.

Do not power displays, high-current LEDs, or audio amplifier from Pi GPIO. Use separate regulated rails and common ground where necessary.

---

# 4. Hardware architecture

## 4.1 Door appliance: Raspberry Pi 5

### Required configuration

- Raspberry Pi 5, preferably 8 GB.
- Official 27 W / 5 V 5 A power supply or equivalently engineered supply.
- Active cooler and enclosure ventilation. Add enclosure fan if testing requires it.
- 128 GB microSD for OS/recovery only.
- 256 GB minimum, preferably 512 GB USB 3 SATA SSD for active video cache, recordings, local metadata, and face database.
- Raspberry Pi AI HAT+ 26 TOPS preferred.
- Ethernet required for main network link.

### Why Pi 5

Pi 5 is selected because it provides dual HDMI, two MIPI camera/display transceivers, USB 3, Gigabit Ethernet, PCIe 2.0 x1 for Hailo, and enough CPU for local UI, camera ingest, media routing, recording, and door logic when background workloads are moved elsewhere.

### Important limitations

- AI HAT+ consumes Pi 5 PCIe. Do not plan to use a normal PCIe NVMe HAT in parallel without additional complexity.
- Use USB 3 SSD storage.
- MIPI ports are shared camera/display interfaces. This design uses both MIPI links for cameras and both HDMI outputs for displays.
- Do not use a DSI touchscreen in the two-camera configuration. Use HDMI plus USB touch.
- Pi has no native analog inputs. Use digital I2C/SPI sensors or an ADC.
- Active cooling is required under camera/Hailo/browser/media workloads.
- Do not host BirdNET, Immich, full Home Assistant, historical analysis, or ADS-B decoding on this Pi.

## 4.2 AI accelerator

### Required choice

Use **Raspberry Pi AI HAT+ 26 TOPS**, based on Hailo-8.

### Rationale

The 26 TOPS Hailo-8 provides better durable headroom for continuous face detection, landmarks/alignment, embedding generation, and potential additional vision models. The 13 TOPS Hailo-8L can work for moderate workloads but is less appropriate for the upgraded build.

The 40 TOPS AI HAT+ 2 is not the default choice. It is more oriented toward local generative-AI workloads, adds cost, and does not directly improve the core media/door interaction path enough to justify selecting it before the mature Hailo-8 pipeline is proven.

### Critical constraint

The real-time door vision service owns the Hailo device. Do not run multiple unrelated Hailo consumers concurrently unless explicitly tested and supported.

## 4.3 Cameras

Use two CSI camera modules:

1. **Visitor/video camera: Camera Module 3 Wide NoIR**
   - wide visitor framing;
   - video-message capture;
   - live feed;
   - low-light upgrade path with IR illuminator.

2. **Recognition camera: Camera Module 3 Standard**
   - narrower framing and more facial pixels;
   - mounted at upper-chest/face height;
   - aimed at expected visitor standing zone around 0.8–1.8 m from door.

Why two cameras: a wide doorbell-style image is not ideal for reliable recognition. A dedicated standard-lens recognition camera provides larger, more frontal faces.

Prototype compatibility: support `single_camera_mode` and `dual_camera_mode` feature flags. A one-camera prototype may start with the standard camera.

Use `rpicam-apps` / libcamera-based capture, not legacy camera tooling.

## 4.4 Displays

### Large Doorboard

- 21.5–24 inch used/refurbished 1080p IPS VESA monitor.
- HDMI from Pi 5.
- Public ambient dashboard.
- Responsive in portrait/landscape if practical.

### Small DoorPad

- 7 inch HDMI capacitive touchscreen, roughly 1024×600.
- HDMI video and USB touch.
- Public interaction surface.
- No administrative access.

Both displays run local browser-kiosk UI driven by Pi 5.

## 4.5 ESP32-S3 door controller

Hardware attached:

- physical bell button;
- WS2812B LED ring/strip;
- speaker via class-D amplifier;
- piezo/accelerometer for secret knock pattern;
- optional door contact reed switch;
- optional NFC reader;
- optional ambient light sensor.

Responsibilities:

- immediate generic button feedback;
- cached personalized LED/audio profile;
- local knock detection;
- watchdog and telemetry;
- fallback state if Pi offline;
- direct serial or local-network communication with Pi.

For immediate control, preferred transport is UART when physically adjacent. UDP is acceptable. MQTT is acceptable for logging/state, but should not be the only immediate control transport.

## 4.6 Audio

Start with one-way video-message audio:

- USB UAC microphone or I2S microphone;
- small class-D amplifier and salvaged/cheap speaker;
- visitor video plus microphone audio recorded locally.

Two-way intercom is deferred due to echo cancellation, feedback, and WebRTC audio complexity.

## 4.7 Sensors

Required/recommended:

- SCD40/SCD41 CO2/temperature/humidity sensor;
- USB microphone near window for BirdNET-Go;
- door contact reed switch;
- piezo/accelerometer for secret knock.

Optional:

- ambient light sensor;
- PM2.5 sensor;
- VOC sensor;
- RTL-SDR for local aircraft reception.

Avoid passive hallway PIR/mmWave without a clearly consent-compatible use case. Avoid analog gas sensors unless an ADC/calibration plan exists.

## 4.8 Storage

- microSD: OS/recovery/minimal configuration only.
- USB SSD: active recordings, local SQLite, media cache, enrollment data, MediaMTX segments.
- NAS: completed clips, photos, final archives, database backups.

Never use microSD for continuous high-write video or active databases.

---

# 5. Topology and trust boundaries

```text
Hallway-facing door assembly
├── Doorboard monitor
├── DoorPad touchscreen
├── Visitor camera + recognition camera
├── Button / LEDs / speaker / microphone
├── Pi 5 + Hailo + SSD
└── ESP32-S3
          │ Ethernet + power via protected flexible loop
Inside room / trusted zone
├── Intel NUC: Home Assistant, broker, database, API aggregation, background jobs
├── Pi 4: BirdNET-Go
├── Pi 3/4 optional: RTL-SDR / ADS-B
└── NAS: archive/media/backups
```

| Component | Trust level | Notes |
|---|---|---|
| NAS | High | durable archive; do not broadly expose |
| NUC | High | primary secrets/integrations/admin plane |
| Pi 5 door appliance | Medium/low | physically accessible; least privilege |
| ESP32 | Low | hardware control endpoint; no sensitive secrets |
| Public screens | Low | assume anyone can touch/see them |
| QR/PWA visitor endpoints | Low | tokenized/rate-limited |

---

# 6. Performance and responsiveness requirements

## 6.1 Absolute rule

Never wait for remote service or background process before giving feedback at door.

Critical path:

```text
physical button -> ESP32 immediate response -> cached profile if available -> local UI transition
```

Noncritical path:

```text
event log -> NUC -> database -> notification -> NAS sync -> analytics
```

## 6.2 Target latency budget

| Path | Target | Notes |
|---|---:|---|
| button to generic LED/audio | p95 <30 ms | ESP32 local |
| button to cached personalized effect | p95 <100 ms | profile preloaded |
| touchscreen tap to visible local response | p95 <100 ms | local UI/websocket |
| face visible to stable identity | p95 <600 ms | dependent on light/framing |
| bell to visitor mode on large display | p95 <250 ms | local UI |
| bell to recording event | <500 ms | stream already live |
| local live video | target <750 ms | WebRTC/local network |
| NAS upload | noncritical | after interaction |

These must be measured with metrics, not assumed.

## 6.3 Identity-cache behavior

Recognition is proactive, not bell-triggered:

1. recognition camera runs continuously;
2. face pipeline requires stable match over several frames;
3. service writes short-lived `current_visitor` cache;
4. Pi sends profile to ESP32;
5. ESP32 stores profile with expiry;
6. button press uses cache immediately;
7. absent cache means immediate generic greeting;
8. later recognition may update display but never delay initial interaction.

Suggested defaults:

- minimum face size threshold;
- match in 2 of last 3 frames;
- cache lifetime 2.5 seconds;
- greeting cooldown 30 seconds per person;
- unknown faces get generic greeting and no stored identity.

## 6.4 Failure behavior

| Failure | Required behavior |
|---|---|
| NUC offline | Pi UI/local recording continue; data queues |
| NAS offline | Pi stores clips and retries upload |
| Hailo unavailable | generic greeting; no personal behavior |
| camera unavailable | button/UI work; show video unavailable |
| ESP32 offline | Pi shows admin error, do not pretend physical effect occurred |
| Pi restart | ESP32 generic fallback animation/unavailable state |
| internet offline | no impact on core flow |
| storage low | stop recording safely, preserve interaction, alert control plane |

---

# 7. Software architecture

## 7.1 General principles

- local-first;
- explicit service ownership;
- event-driven rather than polling-heavy;
- separation of real-time plane and control plane;
- feature flags for optional integrations;
- health endpoint for every service;
- simulation mode without hardware;
- typed contracts for all events/APIs;
- monotonic timing where possible;
- UTC internally, local timezone only at display boundary.

## 7.2 Monorepo structure

```text
doorboard/
├── README.md
├── ARCHITECTURE.md
├── CONTRIBUTING.md
├── LICENSE
├── docs/
│   ├── adr/
│   ├── hardware/
│   ├── protocols/
│   ├── runbooks/
│   └── ui/
├── apps/
│   ├── door-api/
│   ├── door-ui/
│   ├── door-visiond/
│   ├── door-media/
│   ├── door-sync/
│   ├── control-plane-api/
│   ├── wallboard-worker/
│   └── simulator/
├── firmware/
│   └── esp32-door-controller/
├── packages/
│   ├── contracts/
│   ├── config/
│   ├── observability/
│   ├── auth/
│   ├── media-client/
│   ├── event-client/
│   └── ui-kit/
├── integrations/
│   ├── birdnet/
│   ├── satellites/
│   ├── aircraft/
│   ├── printer/
│   ├── home-assistant/
│   ├── food-recommendation/
│   └── gallery/
├── deploy/
│   ├── pi-door/
│   ├── nuc/
│   ├── pi-bird/
│   ├── pi-adsb/
│   └── nas/
├── infra/
│   ├── compose/
│   ├── systemd/
│   ├── caddy/
│   └── monitoring/
├── scripts/
├── tests/
│   ├── integration/
│   ├── e2e/
│   ├── hardware-in-loop/
│   └── performance/
└── tools/
    ├── enrollment-cli/
    ├── seed-data/
    └── diagnostics/
```

## 7.3 Technology recommendations

### Backend

- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy/Alembic
- asyncio where useful
- pytest
- Ruff and mypy/pyright
- OpenTelemetry-compatible structured logging

Reason: Python aligns well with Hailo/vision tooling, Skyfield, automation, and fast integration work.

### Frontend

- TypeScript
- React + Vite or another maintainable SPA approach
- WebSocket client for real-time events
- shared UI package
- Chromium kiosk deployment

Routes:

- `/wallboard`
- `/doorpad`
- `/visitor`
- `/admin`

### Database

- Pi: SQLite in WAL mode for local events and queue state.
- NUC: PostgreSQL preferred for durable control-plane/history.
- NAS: archive only, never active DB host.

### Messaging

- Mosquitto on NUC.
- UART/UDP between Pi and ESP32 for immediate profile/action events.
- WebSockets between local services and displays.

### Media

- MediaMTX on Pi door appliance.
- `rpicam-apps` / libcamera capture.
- WebRTC for local live viewing.
- H.264 initially for compatibility.
- One encode, many consumers.
- MediaMTX segments plus verified async upload to NAS.

### Vision

- Hailo runtime and maintained official Raspberry Pi/Hailo face-recognition material as starting point.
- Encapsulate all accelerator access inside `door-visiond`.
- Vision modes: disabled, mock, single-camera, dual-camera, hardware.

## 7.4 Service definitions

### `door-visiond`

Purpose: real-time vision.

Inputs:
- recognition-camera frames;
- configuration;
- local enrollment DB.

Outputs:
- face visibility events;
- stable identity events;
- current visitor cache;
- performance metrics;
- ESP32 profile update;
- optional MQTT audit event.

Must not:
- block button interaction;
- wait on NUC;
- retain unknown embeddings;
- make security/access decisions.

Suggested endpoints:
- `GET /health`
- `GET /metrics`
- `GET /current-visitor`
- `POST /enroll`
- `POST /unenroll`
- `POST /privacy-mode`

### `door-media`

Purpose: local media state and MediaMTX integration.

Responsibilities:
- bell/video-message recording control;
- local clip finalization;
- thumbnail generation;
- queueing sync;
- stream endpoint metadata;
- bounded retention and storage monitoring.

Must record locally first and never synchronously wait for NAS in visitor path.

### `door-api`

Purpose: local visitor/session orchestration.

Responsibilities:
- visitor session state machine;
- guestbook/poll/check-in UI data;
- button-event handling;
- privacy mode;
- display state broadcasts;
- limited tokenized visitor endpoint;
- local fallback content.

### `door-ui`

Routes:
- `/wallboard`
- `/doorpad`
- `/visitor`
- `/admin`
- `/diagnostics` admin-only.

Must remain usable using Pi-local APIs only. Public routes cannot show sensitive data.

### `door-sync`

Purpose: async bridge Pi -> NUC/NAS.

Responsibilities:
- upload finalized clips/metadata;
- retry with exponential backoff;
- deduplicate uploads;
- queue visibility;
- checksum verification before cleanup.

### `control-plane-api`

Runs on NUC.

Responsibilities:
- trusted event history;
- Home Assistant bridge;
- roommate/focus/calendar status;
- guestbook/poll/scoreboard/mood logic;
- integrations;
- notifications;
- config distribution to Pi.

### `esp32-door-controller`

Responsibilities:
- debounce button;
- generic immediate feedback;
- active profile cache;
- LED/audio effect;
- local knock detection;
- watchdog fallback;
- health telemetry.

---

# 8. Event contracts

Create typed shared contracts. No per-service ad hoc JSON.

Example button event:

```json
{
  "event_id": "uuid",
  "type": "door.button_pressed",
  "source": "esp32-door-controller",
  "occurred_at": "2026-07-04T12:34:56.123Z",
  "monotonic_ms": 123456789,
  "door_id": "primary",
  "trace_id": "uuid"
}
```

Example stable identity:

```json
{
  "event_id": "uuid",
  "type": "vision.identity_stable",
  "source": "door-visiond",
  "occurred_at": "2026-07-04T12:34:56.123Z",
  "person_id": "opaque-internal-id",
  "display_name": "Alex",
  "confidence": 0.93,
  "expires_at": "2026-07-04T12:34:58.623Z",
  "profile_id": "blue_wave",
  "trace_id": "uuid"
}
```

Example ESP32 profile update:

```json
{
  "event_id": "uuid",
  "type": "door.profile_update",
  "source": "door-visiond",
  "target": "esp32-door-controller",
  "profile_id": "blue_wave",
  "expires_at_monotonic_ms": 123459289,
  "priority": "normal"
}
```

Use opaque IDs internally. Never use someone’s name as primary key.

---

# 9. Visitor session state machine

```text
IDLE
  -> APPROACH_DETECTED
  -> IDENTITY_CACHED (optional)
  -> BUTTON_PRESSED
  -> VISITOR_MODE
  -> RINGING
  -> ANSWERED | UNANSWERED_TIMEOUT
  -> VIDEO_MESSAGE_OFFERED
  -> VIDEO_MESSAGE_RECORDING
  -> VIDEO_MESSAGE_REVIEW
  -> VIDEO_MESSAGE_SAVED
  -> SESSION_END
```

Requirements:

- Button press immediately enters visitor mode.
- Generic feedback occurs before network operation.
- Personalized feedback uses cache only.
- Visitor can skip recording.
- Door contact or owner action can mark answered.
- UI and event logs receive every transition.
- Session survives browser reload gracefully.
- Public session expires/reset automatically.
- Admin actions are separate/authenticated.

---

# 10. Face recognition requirements

- strictly opt-in enrollment;
- local-only processing;
- small known list;
- unknown embeddings never persisted;
- stable match across frames;
- short current-visitor cache;
- personalization only, never access control;
- immediate unenrollment/deletion;
- manual correction workflow;
- diagnostics not public.

Enrollment flow:

1. Admin opens private enrollment route.
2. Consent confirmed.
3. Capture several controlled images under varied lighting/angle.
4. Generate embeddings.
5. Store enrollment record.
6. Assign display profile: name, color, optional audio, consent state, revocation.
7. Test locally.
8. Do not publish face data to public UI.

Implementation constraints:

- start from maintained Pi/Hailo face-recognition example;
- treat demo as reference, not production system;
- pin Hailo runtime/model versions;
- startup compatibility checks;
- mock vision events for tests;
- vision implementation behind replaceable interface for future NUC/Jetson migration.

---

# 11. Video and media requirements

Desired flow:

```text
visitor camera + microphone
  -> camera capture
  -> H.264/AAC-compatible stream
  -> MediaMTX
  -> WebRTC local display/phone
  -> segmented local recording on USB SSD
  -> finalized session clip
  -> asynchronous verified upload to NAS
```

Requirements:

- Camera already streaming before bell press.
- No cold-start capture/encoder on press.
- No separate transcode per client.
- Both displays consume local stream.
- Recording cannot block display playback.
- If NAS down, SSD queue persists.
- Bounded local retention.
- Show free storage and queue age.
- H.264 initial standard.
- WebRTC is primary local live protocol, not HLS.

Version one clips:

- record only on bell/video-message interaction;
- optional pre-roll later;
- short and explicit;
- generate thumbnail;
- store consent/context metadata;
- upload async;
- delete local file only after integrity verification.

---

# 12. UI requirements

## Large Wallboard

Normal mode:

- broad owner/roommate state;
- local time;
- bird summary;
- aircraft summary;
- next ISS/satellite pass;
- printer state/thumbnail;
- mood;
- scoreboard;
- daily food recommendation;
- poll result;
- selected guestbook highlights.

Visitor mode:

- generic/personalized greeting;
- broad availability state;
- ring/video message instructions;
- QR code for private phone interaction;
- poll prompt;
- visual effects.

Never show exact location, full calendar, private entries, full visitor logs, diagnostics, or private photos by default.

## Small DoorPad

Public functions:

- ring;
- leave video message;
- guestbook;
- poll;
- voluntary check-in;
- photo booth trigger if enabled;
- privacy/delete request.

Design: large buttons, visible feedback, automatic reset, no keyboard-heavy flow, hardware bell stays primary.

## Admin

Authenticated route only:

- enrollment;
- profile management;
- media deletion;
- public status changes;
- config;
- diagnostics;
- health/latency metrics;
- maintenance mode.

---

# 13. Background integrations

## BirdNET

Run BirdNET-Go on dedicated Pi 4 near window with USB mic.

Requirements:

- independent service;
- forward summarized data to NUC;
- configurable confidence and regional filtering;
- raw audio off by default;
- daily collage async;
- Wallboard uses summarized data only.

## Satellites and ISS

Use Skyfield plus refreshed CelesTrak data.

Requirements:

- scheduled orbital refresh;
- visible-pass calculation based on elevation, darkness, illumination, local coordinates;
- display selected next pass, not every pass;
- use modern orbital data assumptions;
- NUC calculates, Pi renders.

## Aircraft

MVP: permitted API with geographic bounding and caching. Do not promise route/destination data that source does not provide.

Later: RTL-SDR + dump1090 + Pi-Sky/custom local service on spare Pi.

## Printer

NUC provides read-only printer state and optionally stream. Public screens must not have printer control.

## Focus / Weasley clock

Precedence:

1. manual override;
2. explicit Focus shortcut;
3. explicit voluntary geofence label;
4. calendar inference;
5. unknown/default.

Store only broad labels.

## Mood, scoreboards, guestbook, polls

Use custom data models. Public submissions require rate limit, moderation/deletion path, timestamp, and HTML sanitization.

## Food recommendation

Create provider interface:

```text
FoodRecommendationProvider.get_daily_recommendation() -> Recommendation
```

Use mock provider in development. Do not tightly couple UI to an undocumented existing system.

## Photo booth/gallery

Separate indoor photo booth. Explicit capture. Manual tagging is MVP. Store approved photos in NAS gallery. Public social posting deferred.

---

# 14. Research conclusions: what works and what does not

## Use

### Pi 5

Good dedicated door appliance: displays, MIPI cameras, Ethernet, USB SSD, Hailo, local UI/media.

### Hailo-8 AI HAT+ 26 TOPS

Good accelerated vision choice with existing Pi/Hailo face-recognition starting points. Better headroom than 13 TOPS for upgraded version.

### ESP32-S3

Correct separation for physical real-time response and fallback.

### MediaMTX

Good local open-source media router for publishing, proxying, recording, playback, and WebRTC.

### BirdNET-Go

Good self-hosted Raspberry Pi bird pipeline, but isolate it.

### Home Assistant on NUC

Good integration/automation control plane, not critical door path.

### NAS

Good durable archive, not active recording or DB location.

## Avoid in critical path

- battery door cameras;
- cloud face APIs;
- Frigate as the sole real-time greeting engine on Pi;
- microSD as active video/database store;
- HLS for primary door live UI;
- e-paper for interactive DoorPad;
- heavy background jobs on Pi door appliance;
- raw location publishing.

---

# 15. Deployment design

## Door Pi

Raspberry Pi OS 64-bit. Prefer systemd for critical hardware-facing services. Containers are acceptable where hardware access/recovery are proven.

Potential systemd services:

- `door-visiond`;
- `door-media`;
- `door-api`;
- `chromium-kiosk`;
- health/watchdog agent;
- MediaMTX native binary or container after testing.

Requirements:

- automatic boot to operational state;
- kiosk launch after local API ready;
- restart on failure;
- offline boot works;
- health in admin UI;
- versioned config; secrets separate from git.

## NUC

Docker Compose likely appropriate:

- Home Assistant;
- Mosquitto;
- PostgreSQL;
- control plane API;
- workers;
- Caddy/reverse proxy if internal HTTPS used;
- monitoring optional.

## NAS

NUC preferred as sync/control intermediary where practical. Pi uploads with limited access only. Schedule and test backups.

---

# 16. Security requirements

- public and admin routes separated;
- do not expose raw MediaMTX, RTSP, SSH, or Hailo ports broadly;
- no direct public internet exposure;
- VLAN/firewall segmentation if available;
- device-specific credentials;
- no secrets in git;
- `.env.example` only;
- rotate tokens;
- tokenized/rate-limited QR endpoints;
- sanitize user-generated content;
- prevent wallboard untrusted HTML;
- write deployment security checklist.

---

# 17. Observability and testing

Metrics:

- button-to-feedback latency;
- face-visible-to-identity latency;
- cache hit rate;
- stream latency;
- WebRTC clients;
- recording write speed;
- SSD free space;
- NAS queue age;
- Hailo inference duration;
- CPU/temp;
- ESP32 uptime;
- camera frame drops;
- service health;
- UI reconnects.

Logs: structured JSON with service, event ID, trace ID, session ID, timestamp, latency, error. Never log raw biometric data.

Test layers:

- unit tests for state machine, profile cache, permission rules, queue, models;
- integration tests with mocked MQTT/UART/MediaMTX;
- browser E2E for Wallboard/DoorPad/session;
- simulator for button, vision, camera, NUC outages;
- hardware-in-loop for ESP32;
- performance suite with p50/p95/p99.

Mandatory acceptance test before ambient features:

30 minutes with both cameras, Hailo, two kiosk screens, WebRTC, repeated button presses, local recording, face events, and a forced NAS outage/reconnect. It must show no thermal throttling, no growing backlog, no missed button events, stable UI, and p95 targets met.

---

# 18. Milestones

## M0: Repository/bootstrap

- monorepo;
- CI;
- lint/typecheck/tests;
- local dev stack;
- simulator;
- ADRs;
- mock Wallboard/DoorPad;
- fake button event transitions UI.

## M1: Real-time I/O

- ESP32 button/LED/speaker;
- Pi profile updates;
- fallback behavior;
- target physical response latency.

## M2: Camera/media

- one camera;
- MediaMTX;
- local WebRTC;
- SSD recording;
- video route;
- thermal/CPU tests.

## M3: Face recognition

- Hailo pipeline;
- enrollment;
- stable cache;
- ESP32 personalization;
- unknown-face privacy behavior.

## M4: Visitor session

- DoorPad workflow;
- Wallboard visitor takeover;
- video message;
- guestbook/poll;
- session state machine.

## M5: Control plane

- NUC/Home Assistant;
- statuses;
- DB;
- notifications;
- NAS sync.

## M6: Ambient features

- BirdNET;
- satellites;
- aircraft;
- printer;
- mood/scoreboard;
- food provider;
- photo booth.

## M7: Hardening

- security;
- monitoring;
- backup;
- mechanical install;
- recovery runbooks;
- performance tuning.

---

# 19. Explicit instructions for Codex

Codex should make implementation decisions inside these constraints.

Codex must:

1. Create monorepo, README, architecture documentation, ADRs.
2. Create simulated development environment before hardware-specific code.
3. Define typed events before business logic.
4. Implement visitor state machine explicitly.
5. Build feature flags/mock adapters.
6. Treat camera, Hailo, ESP32, MediaMTX, NAS, Home Assistant, BirdNET, and food data as adapters behind interfaces.
7. Test privacy, retention, queue, and session logic.
8. Add performance benchmark harness early.
9. Use secure config/secrets pattern.
10. Keep door real-time path local/bounded.
11. Keep nonessential jobs off door Pi.
12. Make hardware optional in local dev/CI.
13. Write boot/repair/replacement/storage/network/NUC-outage runbooks.

Codex must not:

1. Build one monolith.
2. Make button feedback depend on NUC HTTP.
3. Make response depend on NAS write success.
4. Use cloud face APIs.
5. Retain all raw media forever by default.
6. Use microSD for active recording/DB.
7. Treat face matching as authorization.
8. Couple core design directly to unwrapped vendor demos.
9. Expose private data publicly.
10. Assume Hailo/MediaMTX/CSI performance without acceptance test.

---

# 20. Research references

Validate exact versions when implementation begins.

- Raspberry Pi 5 product brief: https://pip.raspberrypi.com/documents/RP-008348-DS-raspberry-pi-5-product-brief.pdf
- Raspberry Pi hardware documentation: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html
- Camera software: https://www.raspberrypi.com/documentation/computers/camera_software.html
- Camera Module 3: https://www.raspberrypi.com/products/camera-module-3/
- AI HAT+ documentation: https://www.raspberrypi.com/documentation/accessories/ai-hat-plus.html
- Official Pi Hailo face-recogniser: https://github.com/raspberrypi/face_recogniser
- Hailo Pi 5 examples: https://github.com/hailo-ai/hailo-rpi5-examples
- MediaMTX intro: https://mediamtx.org/docs/kickoff/introduction
- MediaMTX recording: https://mediamtx.org/docs/features/record
- MediaMTX publishing: https://mediamtx.org/docs/features/publish
- BirdNET-Go: https://github.com/tphakala/birdnet-go
- Skyfield: https://rhodesmill.org/skyfield/earth-satellites.html
- CelesTrak: https://celestrak.org/
- OpenSky API: https://openskynetwork.github.io/opensky-api/rest.html
- Home Assistant: https://www.home-assistant.io/
- ESPHome: https://esphome.io/

---

# 21. Final project definition

This is not just a smart doorbell. It is a locally hosted, door-mounted interactive room interface with a real-time local interaction plane, a self-hosted smart-room control plane, opt-in social functions, public ambient information, multimedia capture/private archiving, modular hardware, and explicit privacy/reliability boundaries.

The first success criterion is not feature count:

> A person can approach the door, press a physical button, see and hear immediate feedback, interact with two displays, leave a video message, and trigger a personalized opt-in greeting, even if NUC, NAS, or internet is temporarily unavailable.

Everything else is added only after that experience is reliable.
