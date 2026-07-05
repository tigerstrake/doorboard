# packages/event-client — event bus clients

Uniform event plumbing so no service hand-rolls messaging.

- **Python:** async publishers/subscribers for the Pi-local bus (WebSocket/in-process), the SQLite-backed durable queue (used by door-sync), and MQTT (NUC side). Type-filtered subscriptions (`session.*`), automatic envelope validation via contracts, trace-id propagation.
- **TypeScript:** WebSocket client for kiosks per [api-conventions.md](../../docs/protocols/api-conventions.md) — subscribe-with-filters, snapshot-on-connect handling, heartbeat, exponential-backoff reconnect; typed by generated contract types.
- Delivery semantics: at-least-once for durable queues (consumers dedupe by `event_id`), best-effort for live UI fan-out (snapshot recovery covers gaps).
- Mock bus implementation for tests and the simulator.

## ESP32 transport

T-102 implements the Pi-side ESP32 adapter here. `Esp32ProtocolTransport` owns the shared
newline-delimited JSON wire protocol: 512-byte frame cap, ack timeout/retry, `(boot_id, seq)`
dedupe, heartbeat monitoring, link-state notifications, `esp32_link` health, and Prometheus-style
metrics text for service `/metrics` endpoints.

Both production transports share that engine:

- `Esp32ProtocolTransport.open_uart(...)` uses the Python stdlib `asyncio` event loop plus POSIX
  `termios` in 115200 8N1 mode by default. No third-party serial package is introduced for M1; if
  later hardware testing requires `pyserial-asyncio`, that dependency needs the normal escalation.
- `Esp32ProtocolTransport.open_udp(...)` uses `asyncio.DatagramProtocol` with the exact same framing
  and ack core.

Inbound ESP32 wire messages are translated to `packages/contracts` `door.*` events at this boundary
with a fresh trace ID. Outbound helpers accept contract events for `door.profile_update`,
`door.profile_clear`, and `door.effect_play`; TTLs are derived from monotonic time immediately before
transmission.

### Bench note for T-104

Measure profile update round-trip by taking `time.monotonic_ns()` immediately before
`await transport.send_event(door_profile_update_event)` and immediately after it resolves with the
ack. Use an already-started UART link, a warm event loop, and report p50/p95 over at least 500
iterations. This measures only Pi wire encode/write, ESP32 ack handling, and Pi ack resolution, which
is the budget T-104 needs to preserve under 20 ms on bench hardware.
