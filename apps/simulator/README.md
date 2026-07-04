# simulator — hardware-free development environment

**Plane:** dev tooling · **Host:** any laptop / CI · **Language:** Python (+ tiny web control panel) · **Task:** T-003

Makes the entire door experience developable with zero hardware. The simulator stands in for the ESP32, cameras, Hailo, and outage conditions by speaking the real contracts through the real adapter seams.

## Capabilities

- **Fake ESP32:** implements the wire protocol's `mock` transport — emits `button_event`/`knock_event`/heartbeats, accepts `profile_update`/`effect_play`, models the fallback state machine (heartbeat loss, reboot).
- **Fake vision:** scripted sequences of `vision.face_visible` / `identity_stable` / `identity_expired` (e.g. "Alex approaches, stays 4 s, leaves"), honoring cooldown/TTL semantics.
- **Fake media:** stub MediaRouter that "records" placeholder clips with real metadata/checksums so door-sync and retention logic run for real.
- **Outage injection:** kill/restore simulated NUC and NAS reachability; degrade Hailo; fill storage — driving the failure table in ARCHITECTURE.md §10.
- **Scenario runner:** YAML scenario files (`scenarios/`) so tests and demos replay identical sequences; used by integration/e2e suites.
- **Control panel:** minimal local web page with buttons ("press bell", "Alex appears", "NUC down") for interactive dev.

## Rules

The simulator uses `packages/contracts` types and the same adapter interfaces as production — if the simulator needs a hack, the seam is wrong (escalate). CI depends on this app: it must stay fast and dependency-light.
