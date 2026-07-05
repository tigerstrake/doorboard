# packages/event-client - TypeScript event client

Browser-side event client for kiosk routes.

- WebSocket client for kiosks per [api-conventions.md](../../docs/protocols/api-conventions.md):
  subscribe-with-filters, snapshot-on-connect handling, heartbeat, exponential-backoff reconnect,
  typed by generated contract types.
- Delivery semantics: at-least-once for durable queues (consumers dedupe by `event_id`), best-effort for live UI fan-out (snapshot recovery covers gaps).

The Python ESP32 adapter lives in [packages/esp32-link](../esp32-link/README.md).
