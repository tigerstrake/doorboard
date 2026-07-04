# tests

Cross-service test suites (unit tests live inside each app/package). All suites except hardware-in-loop must run with **zero hardware** — CI has no cameras, Hailo, or ESP32.

| Suite | Scope |
|---|---|
| `integration/` | Multi-service flows against the simulator with mocked MQTT/UART/MediaMTX: button→session→recording→sync, outage scenarios from ARCHITECTURE.md §10, privacy-mode propagation, queue durability across restarts |
| `e2e/` | Browser tests (Playwright) for Wallboard/DoorPad/visitor/admin: visitor-mode takeover, session survives reload, auto-reset, no private data on public routes |
| `hardware-in-loop/` | ESP32 protocol conformance and latency measurement against a real board (run manually/bench, never in CI) |
| `performance/` | Latency-budget benchmarks (p50/p95/p99 per §4 path) and the 30-minute M7 acceptance soak: both cameras, Hailo, two kiosks, WebRTC, repeated presses, recording, forced NAS outage/reconnect — no throttling, no backlog growth, no missed buttons, budgets met |

The mandatory acceptance test gates ambient features (M6): it must pass before any M6 task starts on real hardware.
