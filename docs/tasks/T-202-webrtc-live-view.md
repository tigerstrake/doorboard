# T-202: WebRTC live view in door-ui

**Agent:** codex · **Milestone:** M2 · **Depends on:** T-201, T-004
**Why this agent:** WebRTC session handling and reconnect logic is fiddly; latency target is binding.

## Context

WebRTC is the primary local live protocol (not HLS) — ARCHITECTURE.md §6/§11. Consumers: wallboard visitor mode (self-view while recording a message), doorpad video-message preview, owner's phone/admin on the local network.

## Deliverables

- `packages/media-client` (TS): WHEP player wrapper — connect via door-media's published stream metadata, reconnect with backoff, stream-health surface (connected/connecting/unavailable), teardown discipline (no leaked PeerConnections).
- `ui-kit` video component using it: fills tile, shows honest "video unavailable" state (camera-down failure mode from ARCHITECTURE.md §10).
- Integration into doorpad video-message flow position (behind T-402's flow — here just the reusable preview component + a demo route) and admin live-view panel placeholder.
- Python side of `media-client`: stream metadata client used by diagnostics.
- Playwright test against mock/bench stream: player connects, survives door-media restart, reports states correctly.

## Out of scope

Recording UI flow (T-402), phone visitor access (visitor page has no live video in v1), TURN/external access (local network only), HLS anything.

## Acceptance criteria

- Bench: glass-to-glass latency < 750 ms measured (method documented; e.g. clock-in-frame).
- Kill door-media mid-view → component shows unavailable within 5 s, auto-recovers when stream returns; 30-minute soak with zero leaked connections (browser memory/PeerConnection count stable).
- Works on both kiosk viewports and a phone browser on LAN.
