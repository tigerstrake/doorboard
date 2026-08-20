# T-311: Carry identity events from door-visiond to door-api

**Agent:** claude · **Milestone:** M3 · **Depends on:** T-303, T-310 (ADR-0018 shipped)
**Why this agent:** a missing trust-boundary crossing on the recognition path — new route, new
shared secret, and the one claim personalisation reads.

## Context

Reported symptom: walking up to the door produces no greeting on the display.

door-visiond's recognition path was complete and tested end to end *within the process*: a stable
match writes the `current_visitor` cache, pushes `door.profile_update` to the ESP32, and emits
`vision.identity_stable` onto the in-process broadcast queue in
[events.py](../../apps/door-visiond/src/door_visiond/events.py).

Nothing drained that queue, and door-visiond exposes no event stream — so the event never left the
process. door-api, which owns the session machine and the only WebSocket the kiosks connect to,
never saw it; the session stayed `IDLE`; and
[ApproachGreeting](../../apps/door-ui/src/ApproachGreeting.tsx) renders only in
`APPROACH_DETECTED`/`IDENTITY_CACHED`. A recognised person got the door light and a silent screen.

It looked implemented from every angle that did not involve walking up to the door:

- the simulator publishes onto its own in-process bus;
- the dev UI's mock trigger notifies browser-local listeners directly (door-api's `/ws` accepts only
  `subscribe` frames, so even that publish is dropped server-side);
- the MQTT bridge subscribes to `ambient/#` and `status/#` only, never `vision/#`.

ADR-0018 §3 calls the wallboard greeting "the entire point of the feature" and T-303 lists the UI
greeting path as a deliverable. Both assumed this hop existed. This task builds it.

## Deliverables

- `POST /internal/events` on door-api: bearer-token authenticated, `vision.*` only, 202 on accept.
- `EventForwarder` in door-visiond draining the broadcast queue to that route over loopback HTTP,
  best-effort, with backoff so an unreachable door-api costs one attempt per window rather than one
  per event.
- `DOOR_API_INTERNAL_EVENT_TOKEN` on both sides; unset disables forwarding and closes the route.
- `event_forwarding` + counters in door-visiond's `/health` and `/metrics`, so a broken hop is
  visible instead of silent.
- ARCHITECTURE.md §5 records all three legs of a stable identity; §7 records the loopback transport.

## Out of scope

Recognition internals (T-310 fixed the frame-rate starvation), the ESP32 profile path, greeting
visuals, and any change to `packages/contracts` — the envelope already carries everything needed.

## Acceptance criteria

- An enrolled face in front of the camera puts the wallboard into a greeting: verified on the door,
  not only in tests.
- `vision.identity_stable` moves door-api from `IDLE` to `APPROACH_DETECTED`; `identity_expired`
  returns it to `IDLE`.
- The route rejects: no token (401), wrong token (401), unconfigured server (503), and any
  non-`vision.` event type (403) — including `door.button_pressed`, so a token holder still cannot
  fake the door's own inputs.
- A dead door-api leaves recognition, the identity cache, and the ESP32 profile push untouched, and
  the frame loop never blocks on the forwarder.
- `face_visible` is not forwarded (it fires at frame rate and nothing consumes it).
