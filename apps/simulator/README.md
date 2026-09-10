# simulator — hardware-free development environment

**Plane:** dev tooling · **Host:** any laptop / CI · **Language:** Python (+ tiny web control panel) · **Task:** T-003

Makes the entire door experience developable with zero hardware. The simulator stands in for the ESP32, cameras, Hailo, and outage conditions by speaking the real contracts through the real adapter seams.

## Capabilities

- **Fake ESP32:** implements the wire protocol's `mock` transport — emits `button_event`/`knock_event`/heartbeats, accepts `profile_update`/`effect_play`, models the fallback state machine (heartbeat loss, reboot).
- **Fake vision:** scripted sequences of `vision.face_visible` / `identity_stable` / `identity_expired` (e.g. "Alex approaches, stays 4 s, leaves"), honoring cooldown/TTL semantics. While Hailo is down it still sees faces but never produces an identity (ARCHITECTURE.md §10).
- **Fake media:** stub MediaRouter that "records" placeholder clips with real metadata/checksums so door-sync and retention logic run for real.
- **Outage injection:** kill/restore simulated NUC, NAS, and camera reachability; degrade Hailo; fill storage — driving the failure table in ARCHITECTURE.md §10.
- **Scenario runner:** JSON scenario files (`scenarios/`) so tests and demos replay identical sequences; used by integration/e2e suites. Every scenario has a golden event log under `tests/golden/`.
- **Control panel:** minimal local web page with one button per scenario; each runs that scenario headlessly and shows the resulting event log.

## Scenarios and ARCHITECTURE.md §10

| Scenario | Failure row / behaviour it pins |
|---|---|
| `basic-bell`, `known-visitor`, `unknown-visitor`, `full-happy-path` | Normal flows: generic then personalised feedback, bell clip, identity expiry |
| `double-press-abuse`, `identity-churn-abuse`, `press-during-recording` | Abuse and re-entrancy: dedupe, cooldown, recording already in progress |
| `nuc-outage` | NUC offline: button and local recording continue |
| `nas-outage` | NAS offline: clips stay on the SSD, `media.storage_status` shows the backlog |
| `hailo-down-known-visitor` | Hailo unavailable: a face is seen, no identity, no `profile_update`, generic feedback only |
| `camera-unavailable` | Camera unavailable: door-media reports `degraded`, the button still works, nothing is recorded |
| `esp32-silent` | Pi restart: the Pi stops heartbeating, the ESP32 reports `fallback_active`, a press still gives generic feedback, the Pi's `hello` restores it |
| `storage-low` | Storage low: recording stops, the interaction is preserved, `system.storage_alert` is raised |

Scenario actions: `pi_hello`, `pi_heartbeat`, `esp32_heartbeat`, `button`, `known_visitor`, `unknown_visitor`, `identity_expired`, `record_bell`, `hailo_down`/`hailo_up`, `camera_down`/`camera_up`, `nuc_down`/`nuc_up`, `nas_down`/`nas_up`, `storage_full`/`storage_ok`. Each step carries an `at_ms` on the simulated monotonic clock.

## Rules

The simulator uses `packages/contracts` types and the same adapter interfaces as production — if the simulator needs a hack, the seam is wrong (escalate). CI depends on this app: it must stay fast and dependency-light.

## Usage

Run a deterministic headless scenario:

```bash
uv run doorboard-sim run basic-bell
```

List scenarios:

```bash
uv run doorboard-sim list
```

Regenerate a golden log after an intentional change (then review the diff):

```bash
uv run doorboard-sim run basic-bell --output apps/simulator/tests/golden/basic-bell.json
```

Start the local control panel:

```bash
scripts/dev-up --host 127.0.0.1 --port 8765
```
