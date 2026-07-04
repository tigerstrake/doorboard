# ADR-0006: Pi↔ESP32 transport — UART primary, UDP fallback, MQTT never for immediate control

**Status:** Accepted · **Date:** 2026-07-04

## Context

The ESP32 must deliver generic button feedback in <30 ms entirely locally, and personalized feedback in <100 ms using a pre-pushed profile cache. The Pi and ESP32 are physically adjacent inside the door enclosure. MQTT routes through the NUC broker, which is in the untrusted-availability control plane.

## Decision

- **Primary transport: UART** between Pi 5 and ESP32-S3 (they share an enclosure; wire length is centimeters). Newline-delimited JSON frames per [docs/protocols/esp32-pi-protocol.md](../protocols/esp32-pi-protocol.md), with protocol version, sequence numbers, acks for state-bearing messages, and 1 Hz heartbeats both ways.
- **Fallback transport: UDP** on the local link, same message schema, for bench setups where UART wiring is impractical.
- **MQTT is permitted only** for non-critical telemetry/audit mirroring — never as the sole path for profile updates or any message the button flow depends on.
- The ESP32 treats loss of heartbeat (>5 s) as "Pi offline" and enters fallback mode (generic animation, unavailable state); it never blocks button feedback on transport state.

## Consequences

- The transport sits behind an `Esp32Transport` adapter interface (`uart | udp | mock`) so dev/CI run without hardware.
- Latency measurements (T-104) instrument this link; the profile-update path must round-trip well inside the 100 ms budget.
- ESPHome is not used for the door controller: custom firmware gives control over debounce, caching, and fallback semantics that the latency budget requires. (ESPHome remains fine for unrelated room sensors on the control plane.)
