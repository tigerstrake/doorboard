# T-101: ESP32 firmware core

**Agent:** codex · **Milestone:** M1 · **Depends on:** T-002
**Why this agent:** hard-real-time firmware with the tightest latency budget in the system.

## Context

Spec: [firmware/esp32-door-controller/README.md](../../firmware/esp32-door-controller/README.md), wire protocol: [esp32-pi-protocol.md](../protocols/esp32-pi-protocol.md), transport decision: ADR-0006. ESP-IDF/FreeRTOS per ADR-0003. The simulator's fake ESP32 (T-003) is the protocol reference — match its conformance suite.

## Deliverables

- ESP-IDF project skeleton with the four-task architecture (input/effects/link/sensors) and documented pinout header.
- Button path: ISR + debounce → generic LED/audio feedback **< 30 ms**, before any transmission; then `button_event` over the link with cached-profile info.
- Profile cache: single active `{profile_id, monotonic expiry}`; `profile_update`/`profile_clear` handling; expired-cache = generic behavior.
- Wire protocol implementation as a **pure C module** (no FreeRTOS/IDF includes) with host-side unit tests: framing, acks, retransmit (3×/50 ms), `(boot_id, seq)` dedupe, version rejection.
- Heartbeats both ways; Pi-loss fallback (> 5 s → fallback animation, `fallback_active` flag); hardware watchdog; reboot lands in working fallback state.
- Basic effects: at least `generic_press`, `fallback`, and two profile effects as placeholders (full library is T-103).
- Knock detection stub: piezo sampling task + threshold event behind a compile-time flag (real pattern matching deferred to a later brief).

## Out of scope

Full effects library (T-103), Pi-side code (T-102), NFC/reed/light sensors (feature-flagged, later), OTA updates (M7 consideration).

## Acceptance criteria

- Host-side protocol tests pass the same conformance suite as the simulator's fake ESP32.
- On-bench: logic-analyzer or scope trace showing button→LED < 30 ms (attach capture to PR); watchdog recovery and Pi-loss fallback demonstrated (procedure documented for `tests/hardware-in-loop/`).
- Firmware builds reproducibly with pinned ESP-IDF version documented.
