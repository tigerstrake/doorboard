# ADR-0011: Pi-local display-control contract (`display.wallboard_view_requested`)

**Status:** Accepted · **Date:** 2026-07-18

Resolves escalation #93 (raised from T-405). Supersedes the interim local/mock
model that T-405 shipped in `docs/tasks/T-405-wallboard-control-escalation.md`.

## Context

The 7" DoorPad must be able to ask the 1080p Wallboard to switch between the
ambient grid and a focused channel view (aircraft, satellite, scoreboard, birds,
printer, food, poll, guestbook, moments). Both surfaces run on the Pi-local door
plane; the request must survive a Wallboard reload and drive real two-screen
hardware.

`ARCHITECTURE.md` §7 and ADR-0003 require every inter-service message to flow
through `packages/contracts`; `docs/protocols/events.md` is normative and states
"ad hoc JSON is forbidden" and "changes to this file require an ADR." There is no
`display.*` event today. T-405 correctly stopped at typed, browser-local
optimistic state and opened this escalation rather than hand-rolling transport.

Two alternatives were rejected during the escalation:

- **Reuse a `session.*` event.** The session state machine is visitor-owned;
  coupling wallboard display mode to the visitor lifecycle is semantically wrong
  and would let display changes mutate session state.
- **Keep launcher state browser-local forever.** Cannot satisfy the two-screen
  hardware requirement and cannot survive a Wallboard reload.

## Decision

- **Add a `display.*` namespace** to `docs/protocols/events.md` and generate it
  into `packages/contracts` (T-002 pipeline), with one event:

  `display.wallboard_view_requested` — Pi → Wallboard (door plane broadcast).

  Payload (the standard envelope already carries `event_id`, `source`,
  `monotonic_ms`, `door_id`, `trace_id` — the payload does **not** duplicate
  them):

  | Field | Type | Notes |
  |---|---|---|
  | `surface` | `"doorpad"` | requesting surface; only `doorpad` in v1 |
  | `mode` | `"ambient" \| "focus"` | target wallboard mode |
  | `channel` | enum \| `null` | one of `aircraft, satellite, scoreboard, birds, printer, food, poll, guestbook, moments`; **must be `null` when `mode == "ambient"`** and non-null when `mode == "focus"` |
  | `duration_ms` | `int \| null` | optional focus hold; `null` uses the wallboard's default idle timeout |

  The escalation's `request_id` maps to the envelope `event_id`; its
  `requested_at_monotonic_ms` maps to the envelope `monotonic_ms`. No new
  identifier fields are introduced.

- **Transport is Pi-local broadcast only.** The event rides the existing
  door-plane broadcast path (snapshot-on-connect + deltas), same as other
  `door.*`/`session.*` traffic. It must never traverse the NUC/control plane or
  any cloud path — display control is latency-local and privacy-local.

- **Wallboard is the only consumer.** It renders only summary data already
  permitted by `docs/ui/wallboard.md`; a focus request never authorizes new data.
  On an unknown/stale/invalid request, or on `duration_ms`/idle expiry, the
  Wallboard falls back to ambient.

- **DoorPad feedback stays optimistic and local.** The tap advances the DoorPad
  immediately; transport success/failure is surfaced as a subtle status, never as
  the first visible response (preserves the T-405 kiosk-latency invariant).

- **A `mock` adapter** keeps dev/CI and the DoorPad launcher demo working without
  two physical screens, matching the `uart | udp | mock` pattern from ADR-0006.

## Consequences

- `packages/contracts` gains the `display.wallboard_view_requested` type and
  `check-generated-contracts` covers it; `docs/protocols/events.md` gains the
  `display.*` section.
- door-api (or the broadcast owner) publishes the event on the door plane; the
  Wallboard subscribes. No new service and no new dependency.
- The event is not acked as state-bearing: it is a request, and the Wallboard's
  rendered mode is the source of truth. Lost requests degrade to ambient rather
  than wedging either screen.
- T-405's local/mock launcher can be re-pointed at this contract once it lands;
  until then the interim escalation doc remains the reference and no ad hoc JSON
  is emitted.
- Adding future display commands (e.g. brightness, split views) extends the
  `display.*` namespace under a follow-up ADR, not by overloading this event.
