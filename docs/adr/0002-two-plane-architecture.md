# ADR-0002: Two-plane architecture (real-time door plane vs. control plane)

**Status:** Accepted · **Date:** 2026-07-04

## Context

The handoff document's absolute rule: any interaction at the physical door must react immediately and must not depend on the NUC, NAS, cloud, Home Assistant, background jobs, or fresh AI inference. Simultaneously, the system wants rich integrations (HA, Postgres history, BirdNET, satellites, notifications) that are inherently slow, stateful, or network-dependent.

## Decision

The system is permanently split into two planes:

1. **Real-time door plane** — ESP32-S3 + door Pi 5 (door-visiond, door-media, door-api, door-ui, door-sync). Latency-critical, functions standalone, holds only limited-scope credentials, degrades gracefully per ARCHITECTURE.md §10.
2. **Control plane** — NUC (control-plane-api, PostgreSQL, Mosquitto, Home Assistant, wallboard-worker), peripheral Pis, NAS. Latency-tolerant, holds all admin secrets, consumes the door plane's event stream asynchronously.

The door plane never blocks on the control plane. The control plane influences the door plane only through pre-distributed config and cached data (e.g., ESP32 profile cache, wallboard data cache).

## Consequences

- Every feature must be classified into a plane before implementation; briefs state the plane.
- The door Pi runs no heavy background jobs (no BirdNET, no Immich, no full HA, no ADS-B decoding).
- Cross-plane communication is async (event queue in SQLite on Pi, MQTT/HTTP on NUC side).
- A NUC or NAS outage is a supported, tested operating mode — the simulator and test suites must cover it.
