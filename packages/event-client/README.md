# packages/event-client — event bus clients

Uniform event plumbing so no service hand-rolls messaging.

- **Python:** async publishers/subscribers for the Pi-local bus (WebSocket/in-process), the SQLite-backed durable queue (used by door-sync), and MQTT (NUC side). Type-filtered subscriptions (`session.*`), automatic envelope validation via contracts, trace-id propagation.
- **TypeScript:** WebSocket client for kiosks per [api-conventions.md](../../docs/protocols/api-conventions.md) — subscribe-with-filters, snapshot-on-connect handling, heartbeat, exponential-backoff reconnect; typed by generated contract types.
- Delivery semantics: at-least-once for durable queues (consumers dedupe by `event_id`), best-effort for live UI fan-out (snapshot recovery covers gaps).
- Mock bus implementation for tests and the simulator.
