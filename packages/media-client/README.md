# packages/media-client — media consumption helpers

Client-side helpers for talking to door-media/MediaMTX (used by door-ui, tools, tests).

- TypeScript: WebRTC (WHEP) player component wiring for kiosks/phones — connect, reconnect with backoff, stream-health surface; consumed via `ui-kit`.
- Python: thin client for door-media's HTTP API (stream metadata, recordings list) used by door-sync, diagnostics, and tests.
- Mock stream source for dev/CI (colour-bars/placeholder), so UI work never requires a camera.
- No direct MediaMTX/RTSP access from clients — everything goes through door-media's published endpoints.

## TypeScript WHEP player

`createWhepPlayer()` polls door-media's `GET /streams` metadata, connects to the
selected stream's WHEP endpoint, and reports `connecting`, `connected`, or
`unavailable`. It tears down the current `RTCPeerConnection` before every
reconnect and on component unmount. In mock mode, door-media publishes a
`/mock/whep/...` URL; the browser client uses that as a synthetic local stream
while still polling door-media health, so killing door-media makes the preview
go unavailable and recovery reconnects automatically.

Door-ui uses `/door-media/streams` as the same-origin metadata URL. In Vite dev
and Playwright, that path proxies to the mock door-media service. On the Pi,
Caddy/systemd should expose the same local path to door-media.

## Python stream metadata client

Diagnostics can use:

```python
from doorboard_media_client import DoorMediaClient

stream = DoorMediaClient("http://127.0.0.1:8082").get_stream("visitor")
```

The client rejects well-formed but invalid stream metadata, including negative
client counts or non-boolean `stream_up`, so diagnostics do not report a lying
media state.

## Latency bench method

Mock CI cannot measure real glass-to-glass latency. The bench check for the
`<750 ms` WebRTC target is:

1. Show a monotonic millisecond clock in the visitor camera frame.
2. Open `/live-view-demo` on each target viewport: wallboard, doorpad, and a LAN phone.
3. Record the physical display with a high-frame-rate camera.
4. For at least 50 samples, subtract the in-frame clock value from the display capture timestamp.
5. Report p50/p95/p99 and fail the bench if p95 is `>= 750 ms`.

The Playwright mock test covers connection lifecycle, honest unavailable state
within five seconds, automatic recovery, and stable PeerConnection count.
