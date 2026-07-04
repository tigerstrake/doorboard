# T-004: Mock Wallboard/DoorPad UI shell + ui-kit seed

**Agent:** gemini · **Milestone:** M0 · **Depends on:** T-002
**Why this agent:** high-volume component boilerplate against precise specs.

## Context

Specs: [docs/ui/wallboard.md](../ui/wallboard.md), [docs/ui/doorpad.md](../ui/doorpad.md), [packages/ui-kit/README.md](../../packages/ui-kit/README.md), [apps/door-ui/README.md](../../apps/door-ui/README.md). Goal (handoff M0): mock Wallboard/DoorPad where a fake button event transitions the UI. Follow the specs literally; where a visual choice is unspecified, keep it simple and dark-themed.

## Deliverables

- `door-ui` routes `/wallboard`, `/doorpad`, `/visitor`, `/admin` (visitor/admin are labeled placeholders only).
- `ui-kit` seed components: tile (with `as_of` staleness prop), status badge (all eight presence labels, fixed colors/icons), big-button, greeting banner (generic + accent-colored variants), countdown/auto-reset wrapper, QR placeholder. Storybook/Ladle catalog included.
- Wallboard ambient mode rendering every tile from the spec with static fixture data from `tools/seed-data`.
- Wallboard visitor-mode takeover + DoorPad home screen (all seven actions as buttons; flows behind them are placeholders) — driven by `session.state_changed` events over a WebSocket connection to the simulator's control panel feed (or a local mock socket if the simulator lands later; keep the client behind `packages/event-client`'s TS interface).
- Auto-reset to ambient/home after inactivity.

## Out of scope

Real session logic (T-401), video (T-202/T-402), admin UI (T-304+), social submissions (T-403), any backend code, any new dependencies beyond React/Vite/Storybook-or-Ladle.

## Acceptance criteria

- Simulator "press bell" (or mock trigger) → wallboard flips to visitor mode and DoorPad reacts, visibly, without page reload; reset returns both to idle.
- Every ui-kit component escapes text content (test with a `<script>` string fixture).
- Renders correctly at 1920×1080 (wallboard, landscape + portrait) and 1024×600 (doorpad); touch targets ≥ 48 px.
- `tsc --noEmit`, ESLint, and component tests green; no console errors in a 5-minute idle soak.
