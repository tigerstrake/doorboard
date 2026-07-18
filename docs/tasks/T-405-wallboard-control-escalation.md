# Escalation from T-405: wallboard display-control contract

## Blocked task

T-405

## What the brief/architecture says vs. what you found

DoorPad-to-Wallboard remote control requires a Pi-local, typed command so the 7" DoorPad can request the 1080p Wallboard to show ambient grid or a focused channel view. `ARCHITECTURE.md` §7 and ADR-0003 require every inter-service message to use `packages/contracts`; `docs/protocols/events.md` has no `display.*` event today.

The UI can render optimistic local feedback and mock-mode focused views, but it must not emit ad hoc JSON over the broadcast path.

## Options considered

1. Add `display.wallboard_view_requested` to the event catalog and generated contracts.
   - Pros: explicit command type, local-only broadcast path, testable by snapshot-on-connect and deltas.
   - Cons: requires ADR/Claude sign-off because it expands the event catalog.

2. Reuse a `session.*` event for display focus.
   - Pros: no new namespace.
   - Cons: semantically wrong; session state machine is visitor-owned and would couple unrelated wallboard display mode to visitor lifecycle.

3. Keep launcher state browser-local only.
   - Pros: lets UI mock mode demonstrate the interaction safely.
   - Cons: does not satisfy the real two-screen hardware requirement and cannot survive a Wallboard reload.

## Proposed contract shape

```text
display.wallboard_view_requested
  request_id: uuid
  surface: "doorpad"
  mode: "ambient" | "focus"
  channel: "aircraft" | "satellite" | "scoreboard" | "birds" | "printer" | "food" | "poll" | "guestbook" | "moments" | null
  duration_ms: int | null
  requested_at_monotonic_ms: int
```

Validation notes:

- `channel` must be `null` when `mode` is `ambient`.
- Public focused views render only summary data allowed by `docs/ui/wallboard.md`.
- DoorPad visible feedback remains optimistic and local; transport success/failure is surfaced as a subtle status, not as the first visible response.
- Wallboard falls back to ambient on timeout or invalid/stale request.

## What is blocked

Backend transport and `packages/contracts` changes are blocked until ADR/Claude approval. T-405 can proceed with visual redesign, public-flow fixes, tests, and mock-mode local state for focused views.
