# T-003: Simulator — hardware-free development environment

**Agent:** codex · **Milestone:** M0 · **Depends on:** T-002
**Why this agent:** the simulator defines the adapter seams every hardware component hides behind; wrong seams here poison every later task.

## Context

Spec: [apps/simulator/README.md](../../apps/simulator/README.md). The handoff mandates a simulated dev environment before any hardware-specific code (§19.2). Everything speaks `packages/contracts`.

## Deliverables

- Adapter interfaces (in the packages where consumers will import them): `Esp32Transport` (per [esp32-pi-protocol.md](../protocols/esp32-pi-protocol.md) §adapter), `VisionPipeline`, `MediaRouter` — each with the `mock` implementation living in/driven by the simulator.
- Fake ESP32: full wire-protocol state machine (hello/heartbeat/ack/retransmit/dedupe, profile cache with TTL, fallback on heartbeat loss) — this doubles as the protocol reference implementation.
- Fake vision: scenario-scripted `face_visible`/`identity_stable`/`identity_expired` with cooldown and TTL semantics.
- Fake media: records placeholder clips with real metadata + sha256 so retention/sync logic downstream is exercised for real.
- Outage injection: NUC/NAS reachability toggles, Hailo degradation, storage-full simulation.
- Scenario runner: YAML scenarios in `apps/simulator/scenarios/` (ship at least: `basic-bell`, `known-visitor`, `unknown-visitor`, `nuc-outage`, `storage-low`); CLI to run them; used later by `tests/integration/`.
- Control panel: minimal local web page with manual triggers (buttons for bell, arrivals, outages).
- `scripts/dev-up` starts the simulator and (as they come to exist) the door services in mock mode.

## Out of scope

Real UART/UDP transport (T-102), real vision (T-302), real MediaMTX (T-201), UI beyond the bare control panel.

## Acceptance criteria

- All five scenarios run headless and produce deterministic event logs (golden-file tested).
- Fake ESP32 passes a protocol conformance test suite (acks, retransmit dedupe, TTL expiry, fallback timing) — these tests will later run against real firmware in `tests/hardware-in-loop/`.
- Runs on a laptop with no hardware, no network beyond localhost, and in CI.
