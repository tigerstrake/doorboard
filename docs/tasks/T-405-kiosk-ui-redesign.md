# T-405: Kiosk UI audit, redesign, and wallboard launcher

**Agent:** codex · **Milestone:** M4 · **Depends on:** T-401, T-402, T-403, T-404
**Why this agent:** touches latency-sensitive public kiosk flows, public-surface privacy constraints, and a new display-control contract that must stay Pi-local.

## Context

Specs: [docs/ui/wallboard.md](../ui/wallboard.md), [docs/ui/doorpad.md](../ui/doorpad.md), [docs/ui/visitor.md](../ui/visitor.md), [docs/ui/admin.md](../ui/admin.md). Architecture constraints: [ARCHITECTURE.md](../../ARCHITECTURE.md) §1, §4, §5, §8, §9, §10. Event and API rules: [docs/protocols/events.md](../protocols/events.md), [docs/protocols/api-conventions.md](../protocols/api-conventions.md).

The current Doorboard UI is functional but still reads as a prototype: public routes expose development scaffolding, DoorPad post-ring behavior is a dead-end instead of a guided flow, some public confirmation buttons report success without performing the durable action, the four surfaces lack a coherent visual system, and all major routes are concentrated in `apps/door-ui/src/App.tsx`.

This task is a focused UI/UX redesign pass across the four existing surfaces plus a mock-mode wallboard launcher. It does not authorize ad hoc event JSON or backend transport changes for display control.

## Deliverables

- Written audit findings captured in this brief/PR: visual defects, interaction defects, privacy risks, empty/loading/error/offline states, accessibility, responsive behavior at kiosk resolutions, and stale-data treatment.
- Shared visual language in `packages/ui-kit`: tokens and reusable primitives for kiosk buttons, cards/tiles, status treatments, focus/active/disabled states, and dark hallway-friendly surfaces.
- DoorPad redesign:
  - cold-start home remains large-button only;
  - any `session.state_changed` into `BUTTON_PRESSED`, `VISITOR_MODE`, or `RINGING` advances the DoorPad to a contextual waiting journey;
  - check-in is prominent immediately after ringing;
  - sensible next actions are sequenced through `RINGING`, `ANSWERED`, `UNANSWERED_TIMEOUT`, `VIDEO_MESSAGE_OFFERED`, recording/review/save, and reset.
- DoorPad launcher surface for wallboard channels: ambient grid, Aircraft/Flights, Satellite pass, Scoreboard, Birds, Printer, Food, Poll, and Guestbook/Moments where enabled. DoorPad feedback is immediate and local; wallboard rendering works in mock mode.
- Wallboard focused/channel views: large, hallway-legible views for launcher selections; automatic ambient fallback after idle; no forbidden public data.
- Public route cleanup: simulation/dev navigation is gated behind an explicit dev/sim flag, `/visitor` renders the real token-minimal page, and public routes do not expose diagnostics/admin escape hatches.
- Guestbook, poll, and deletion confirmation semantics corrected so every success state corresponds to the real local API action. Poll voting must be one clear interaction, not a fake submit button layered over per-option voting.
- Admin/diagnostics polish pass: replace hardcoded fake stat cards with honest mock/unreachable states, improve density and hierarchy without exposing admin routes from public screens.
- App decomposition into route/surface modules where it reduces risk and improves maintainability, while preserving behavior not intentionally redesigned.
- Tests and visual verification for public interaction fixes, wallboard focus rendering, and privacy-sensitive rendering.

## Required escalation

The DoorPad-to-Wallboard remote control requires a new typed display-control event in `packages/contracts` and [docs/protocols/events.md]. This task must open an `escalation` issue for Claude-tier/ADR review proposing the contract shape before relying on transport behavior.

Proposed contract shape for review:

```text
display.wallboard_view_requested
  request_id: uuid
  surface: "doorpad"
  mode: "ambient" | "focus"
  channel: "aircraft" | "satellite" | "scoreboard" | "birds" | "printer" | "food" | "poll" | "guestbook" | "moments" | null
  duration_ms: int | null
  requested_at_monotonic_ms: int
```

Until that ADR lands, UI code may keep a typed local/mock model and optimistic rendering, but must not emit hand-rolled inter-service JSON.

## Out of scope

- Door-api or broadcast transport implementation for display control before the contract ADR is approved.
- New cloud/control-plane dependencies in any public flow.
- New heavy UI libraries, CSS frameworks, or component suites.
- Changes to recognition, enrollment, retention, media recording semantics, or access control.
- New ambient integrations beyond rendering the existing summaries/fixtures.
- Door unlocking, two-way intercom, live visitor-phone video, or exact-location/calendar display.

## Acceptance criteria

- `pnpm typecheck`, `pnpm test`, and `apps/door-ui` Playwright tests pass; snapshots updated only for intentional visual changes.
- Mock mode demonstrates: bell/session event → wallboard visitor mode, DoorPad post-ring check-in prompt, DoorPad launcher → wallboard focused view, guestbook/poll/deletion real submission paths, and offline/stale hints.
- DoorPad tap feedback is visibly local/optimistic and does not wait for HTTP/WebSocket completion.
- Public routes do not render simulation controls unless explicitly enabled and never show forbidden privacy content.
- Wallboard focused views auto-return to ambient after an idle timeout and remain legible at 1920×1080.
- DoorPad touch targets are at least 48 px at 1024×600, with visible focus/active/disabled states and reduced-motion-safe transitions.

## UX audit findings and disposition

The implementation pass audited each action through its next useful choice, including loading,
offline, timeout, duplicate-action, invalid-data, and storage-unavailable behavior.

- **Ring and waiting:** the old ring acknowledgement was a dead end. Ring now advances locally to
  a DoorPad-only live self-view with an explicit waiting state, a primary “Wait for Someone to
  Open” choice, immediate video-message, check-in, phone handoff, guestbook, poll, and end-session
  actions. The wallboard remains camera-free and the phone route does not gain live video.
- **Answer outcomes:** “Someone Is Coming,” “Can’t Answer,” and “End Session” now perform real
  authenticated/session API transitions. An answered session ends instead of misleadingly offering
  a video message. API rejection is shown as a retryable failure rather than optimistic success.
- **Media:** video and photo actions no longer advance until the local media/session API accepts the
  transition. Save, discard, review, retry, recording limits, storage pause, and playback failure all
  retain a truthful recovery path. Auto-reset pauses during consent, recording, and review.
- **Phone handoff:** the signed QR page is deliberately limited to status, text note, poll, and
  deletion. It clearly directs video recording back to the DoorPad and handles expiry without
  exposing another route or token.
- **Social actions:** canned guestbook text and poll choices require explicit confirmation. Results
  appear only after a vote. Character limits, duplicate submits, service-unavailable states, and
  same-session deletion are visible and actionable.
- **Identity and privacy:** public surfaces never show opaque profile/person identifiers. Named
  check-in is offered only when a consented display name exists. The camera notice remains behind
  **Privacy & Info** by product decision; it is not persistently shown in the DoorPad footer.
- **Ambient wallboard:** presence, mood, scoreboard, birds, aircraft, satellite, printer, and food
  consume existing typed events. Production does not present fixtures as live data. Tiles show
  empty/unavailable/stale state, and bounded alert priority lets an overhead aircraft displace a
  lower-priority bird notice without interrupting visitor mode.
- **Long-running and hostile-input behavior:** presence/scoreboard collections and rendered summary
  lists are bounded; numeric progress/distance values are validated or clamped; external labels are
  trimmed and length-limited; duplicate alerts are rate-limited. A well-formed but unusable update
  therefore cannot permanently stop the event loop or grow the UI without bound.
- **Admin:** diagnostics and media inboxes distinguish loading, empty, stale, and unreachable states.
  Existing bearer authentication protects live controls, playback, and deletion; public navigation
  does not reveal the admin route.
- **Accessibility and layouts:** workflows move focus after screen changes, status announcements use
  polite live regions, controls have visible disabled/focus states, reduced motion is respected, and
  the post-ring journey is covered at the physical 1024×600 DoorPad viewport plus phone and 1080p
  wallboard orientations.
