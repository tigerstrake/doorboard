# packages/media-client — media consumption helpers

Client-side helpers for talking to door-media/MediaMTX (used by door-ui, tools, tests).

- TypeScript: WebRTC (WHEP) player component wiring for kiosks/phones — connect, reconnect with backoff, stream-health surface; consumed via `ui-kit`.
- Python: thin client for door-media's HTTP API (stream metadata, recordings list) used by door-sync, diagnostics, and tests.
- Mock stream source for dev/CI (colour-bars/placeholder), so UI work never requires a camera.
- No direct MediaMTX/RTSP access from clients — everything goes through door-media's published endpoints.
