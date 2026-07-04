# Task briefs — index

One brief per task; each is mirrored by a GitHub issue with the same ID, an `agent:*` label, and a milestone. Workflow rules: [CONTRIBUTING.md](../../CONTRIBUTING.md) / ADR-0008. Dependencies are strict — don't start a task whose dependencies aren't merged.

Briefs for M0–M2 are execution-ready. Briefs for M3+ are complete but get a final Claude-tier confirmation pass when their milestone opens (upstream merges may refine details). Every milestone closes with a `T-x90` review task owned by the Claude tier ([milestone review template](T-x90-milestone-review.md)).

| ID | Task | Agent | Milestone | Depends on |
|---|---|---|---|---|
| [T-000](T-000-monorepo-tooling.md) | Monorepo tooling bootstrap | codex | M0 | — |
| [T-001](T-001-ci-and-repo-hygiene.md) | CI pipeline + repo hygiene | gemini | M0 | T-000 |
| [T-002](T-002-contracts-package.md) | Contracts package (events + schemas + TS types) | codex | M0 | T-000 |
| [T-003](T-003-simulator.md) | Simulator (fake ESP32/vision/media/outages) | codex | M0 | T-002 |
| [T-004](T-004-mock-ui-shell.md) | Mock Wallboard/DoorPad UI shell + ui-kit seed | gemini | M0 | T-002 |
| [T-090](T-x90-milestone-review.md) | M0 review + acceptance | claude | M0 | all M0 |
| [T-101](T-101-esp32-firmware-core.md) | ESP32 firmware core | codex | M1 | T-002 |
| [T-102](T-102-pi-esp32-transport.md) | Pi-side ESP32 transport adapter | codex | M1 | T-002, T-003 |
| [T-103](T-103-esp32-effects-library.md) | LED/audio effects + profile table | gemini | M1 | T-101 |
| [T-104](T-104-latency-harness.md) | Latency measurement harness | codex | M1 | T-101, T-102 |
| [T-190](T-x90-milestone-review.md) | M1 review + physical latency acceptance | claude | M1 | all M1 |
| [T-201](T-201-door-media.md) | door-media + MediaMTX + SSD recording | codex | M2 | T-002, T-003 |
| [T-202](T-202-webrtc-live-view.md) | WebRTC live view in door-ui | codex | M2 | T-201, T-004 |
| [T-203](T-203-media-housekeeping.md) | Thumbnails, retention, storage tiles | gemini | M2 | T-201 |
| [T-290](T-x90-milestone-review.md) | M2 review + thermal/CPU check | claude | M2 | all M2 |
| [T-301](T-301-privacy-enrollment-design.md) | Privacy enforcement + enrollment data model | claude | M3 | T-002 |
| [T-302](T-302-visiond-hailo-pipeline.md) | door-visiond Hailo pipeline | codex | M3 | T-301, T-201 |
| [T-303](T-303-identity-cache.md) | Identity cache + ESP32 personalization | codex | M3 | T-302, T-102 |
| [T-304](T-304-enrollment-ui.md) | Enrollment CLI + admin UI forms | gemini | M3 | T-302 |
| [T-390](T-x90-milestone-review.md) | M3 review + privacy audit | claude | M3 | all M3 |
| [T-401](T-401-session-state-machine.md) | Visitor session state machine (door-api) | codex | M4 | T-002, T-003 |
| [T-402](T-402-doorpad-visitor-flow.md) | DoorPad visitor flow + video message | codex | M4 | T-401, T-201 |
| [T-403](T-403-social-crud.md) | Guestbook/poll/check-in CRUD | gemini | M4 | T-401 |
| [T-404](T-404-wallboard-visitor-mode.md) | Wallboard visitor-mode takeover | gemini | M4 | T-401, T-004 |
| [T-490](T-x90-milestone-review.md) | M4 review + e2e session acceptance | claude | M4 | all M4 |
| [T-501](T-501-control-plane-api.md) | control-plane-api + Postgres + ingestion | codex | M5 | T-002 |
| [T-502](T-502-door-sync.md) | door-sync upload engine | codex | M5 | T-201, T-501 |
| [T-503](T-503-nuc-stack.md) | NUC compose stack + Mosquitto + HA bridge | gemini | M5 | T-501 |
| [T-504](T-504-presence-engine.md) | Presence/Weasley-clock engine | gemini | M5 | T-501 |
| [T-590](T-x90-milestone-review.md) | M5 review + outage drill | claude | M5 | all M5 |
| [T-601](T-601-birdnet-adapter.md) | BirdNET adapter + bird Pi deploy | gemini | M6 | T-501, T-503 |
| [T-602](T-602-satellites.md) | Satellite pass prediction | gemini | M6 | T-501 |
| [T-603](T-603-aircraft.md) | Aircraft adapter (OpenSky) | gemini | M6 | T-501 |
| [T-604](T-604-printer.md) | Printer status adapter | gemini | M6 | T-501 |
| [T-605](T-605-mood-scoreboard-food.md) | Mood/scoreboard/food providers + tiles | gemini | M6 | T-501, T-404 |
| [T-606](T-606-photobooth-gallery.md) | Photo booth + gallery | codex | M6 | T-201, T-502 |
| [T-690](T-x90-milestone-review.md) | M6 review | claude | M6 | all M6 |
| [T-701](T-701-security-review.md) | Security review + deployment checklist | claude | M7 | all M0–M6 |
| [T-702](T-702-performance-acceptance.md) | Performance acceptance suite (30-min soak) | codex | M7 | all M0–M4 |
| [T-703](T-703-runbooks-monitoring.md) | Runbooks + monitoring dashboards + backups | gemini | M7 | T-503 |
| [T-790](T-x90-milestone-review.md) | Final acceptance (success criterion) | claude | M7 | all M7 |

**Gate:** the M7 performance acceptance test (T-702's suite) must pass on real hardware before M6 ambient features are enabled on the door — ambient work may be *developed* earlier against mocks, but not deployed to the appliance before the gate.
