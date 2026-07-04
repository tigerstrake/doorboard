# T-402: DoorPad visitor flow + video message

**Agent:** codex · **Milestone:** M4 · **Depends on:** T-401, T-201
**Why this agent:** ties session, media, and UI together across the touchscreen's latency budget; the video-message flow is the project's marquee interaction.

## Context

Spec: [docs/ui/doorpad.md](../ui/doorpad.md). The DoorPad home shell exists (T-004); this task makes Ring and Leave-a-video-message real, driven by T-401's session machine and T-201's recording control, using T-202's preview component.

## Deliverables

- Ring: touch action → same session path as hardware button (distinct trigger source in events), immediate optimistic UI (<100 ms), ESP32 `effect_play` feedback via door-api.
- Video message flow: offer → countdown → recording (live self-preview via media-client, red indicator, elapsed, max-duration cap from config) → review (playback of the just-recorded clip, re-record, discard) → save (consent-context metadata `visitor_initiated`) → confirmation; skip/abort at every step (handoff §9).
- Session-driven screen routing on the DoorPad: the pad follows `session.state_changed` (a session started by hardware button drives the pad's screens too).
- QR display step where the spec calls for it (token fetched from door-api's visitor-token endpoint).
- Playwright e2e: full flow against simulator + mock media; abandonment paths reset cleanly.

## Out of scope

Guestbook/poll/check-in (T-403), photo booth (T-606), wallboard visuals (T-404), phone `/visitor` page beyond the QR handoff (existing placeholder), two-way audio (excluded from v1 entirely).

## Acceptance criteria

- Tap-to-visible-response p95 < 100 ms on kiosk hardware (T-104 conventions; optimistic rendering — no round-trip gates).
- A saved message appears in the admin inbox with correct metadata, thumbnail, and consent context; a discarded one leaves zero files on disk (verified in test).
- Flow abandonment (walk away at each step) auto-resets within the configured timeout; no stuck sessions after 50 scripted abandonments (soak test).
