# T-102: Pi-side ESP32 transport adapter

**Agent:** codex · **Milestone:** M1 · **Depends on:** T-002, T-003
**Why this agent:** the seam between the real-time link and the async service world; subtle concurrency.

## Context

Implements the `Esp32Transport` interface (defined in T-003) for real hardware: `uart` (primary) and `udp` (fallback) per ADR-0006 and [esp32-pi-protocol.md](../protocols/esp32-pi-protocol.md). Lives in `packages/event-client` (or a dedicated module the brief reviewer approves) and is consumed by door-api and door-visiond.

## Deliverables

- `uart` implementation: asyncio serial (pinned library choice documented), newline-framing with 512-byte cap, malformed-line drop+count, ack tracking with timeout/retransmit, `(boot_id, seq)` dedupe, heartbeat emission/monitoring.
- `udp` implementation sharing the framing/ack core (one protocol engine, two byte-transports).
- Translation boundary: inbound wire messages → `door.*` contract events, exactly once, trace-id assigned here.
- Link-status API (`connected`, `last_heartbeat_mono_ms`, `rx_errors`, `tx_retries`) + `esp32_link` health sub-check and metrics.
- ESP32-offline behavior: surfaced as status, never faked — consumers can subscribe to link-state changes.
- Tests: protocol engine unit tests against recorded byte streams; integration test driving the real engine against the simulator's fake ESP32 over a socketpair/pty.

## Out of scope

Firmware (T-101), what services *do* with events, MQTT mirroring (M5), mutual TLS/link encryption (future ADR if warranted).

## Acceptance criteria

- Same conformance suite as T-003/T-101 passes over a pty against the fake ESP32.
- Fault injection tests: garbage bytes, dropped acks, ESP32 reboot mid-conversation (boot_id change), heartbeat loss → all handled per spec, no unhandled exceptions, no event duplication.
- Round-trip profile_update→ack measured < 20 ms on bench hardware (leaves budget headroom); measurement method documented for T-104 reuse.
