# ADR-0040: the recognition profile push reaches the ESP32 through door-api

**Status:** Accepted · **Date:** 2026-08-23 · **Clarifies:** ARCHITECTURE.md §5.2, §3 (ESP32 row) · **Extends:** ADR-0006 (ESP32 transport), ADR-0018 §3 (the greeting is the point)

## Context

ARCHITECTURE.md §5.2 describes recognition as three legs that must all fire on a stable
match: the ESP32 leg (a `door.profile_update` so the personalized light plays), the
door-api leg (a `vision.identity_stable` so the screen greets by name), and the
`current_visitor` cache. It warns, in as many words, that "a leg that silently does
nothing looks exactly like recognition not working."

The ESP32 leg was that leg. `door-visiond` builds the `door.profile_update` on every
cache refresh (`_on_cache_refresh`) and hands it to `_submit_esp32_profile_event`, which
returns immediately whenever it has no `esp32_transport` — and it never has one in
production. The reason is structural, not a missing setting:

- There is **one** ESP32, on **one** UART, and the firmware compiles in no network stack
  (no wifi, no UDP) — the link is UART-only.
- **door-api owns that UART.** It runs the `Esp32LinkSupervisor`, opens the serial port
  (`ESP32_TRANSPORT=uart`), reads button/knock/contact events off it, and already writes
  to it (the doorpad `effect_play`). `door-visiond` opens no serial port and has no ESP32
  settings; two processes cannot own one `/dev/ttyACM1`.

So §5.2's "door-visiond pushes a `door.profile_update` to the ESP32" was never physically
possible as written. The `esp32_transport` parameter on `VisiondService` is a test seam,
and in production the light leg was dead: a recognised person got the on-screen greeting
(ADR-0018 §3) and their chosen light never played, nor did the button's cached
personalized effect, because the ESP32 profile cache was always empty.

## Decision

**The profile push travels `door-visiond → door-api → ESP32`.** door-visiond still
decides *what* profile and *when* (it owns recognition, the cache, and the 2.5 s TTL);
door-api is the wire, because it owns it.

- door-visiond forwards `door.profile_update` / `door.profile_clear` to door-api over
  loopback HTTP — the same best-effort mechanism as the existing identity-event hop
  (`event_forwarder`) and the purge outbox, gated by the same `DOOR_API_BASE_URL` +
  `DOOR_API_INTERNAL_EVENT_TOKEN`. No new dependency, no broker, nothing off the door.
- door-api exposes `POST /internal/esp32/profile`. It is the mirror image of
  `/internal/events`: that route ingests *inbound* identity claims and refuses `door.*`
  so it can never fake the door's own inputs; this one carries an *outbound* light
  command and accepts **only** the two profile events. Token-required, 202 always.
- door-api relays via the transport's `send_event`, which does the
  `expires_at_monotonic_ms` → `ttl_ms` conversion (the ESP32 re-anchors against its own
  clock) **and** allocates the transport's own sequence number — so the profile push
  shares no seq counter with the doorpad `effect_play` and cannot collide with it.

### Why not derive the push from `identity_stable`, which door-api already receives

Because the cadences differ. `vision.identity_stable` is emitted **at most once per 30 s**
(the greeting cooldown, pipeline P-10), but the ESP32 cache TTL is ~2.5 s and must be
refreshed every couple of seconds or the light lapses. Deriving the push from the
greeting would light the door for 2.5 s and then fall back to generic for the next 27.5 s.
The frequent `_on_cache_refresh` events are the right source, so they are what we relay.

### Throttling the relay

The cache refreshes on every stable-match frame (~10 Hz). Over a UART that is a fine,
cheap cadence; over loopback HTTP it is wasteful. So on the relay path a *same-profile*
(`normal`-priority) refresh is sent at most once per
`VISIOND_ESP32_PROFILE_RELAY_MIN_INTERVAL_MS` (default 1000 ms, ~1 Hz — well inside the
2.5 s TTL), while a *new or changed* profile (`high` priority) goes immediately so the
light appears promptly on recognition. The direct-transport path (tests) is unthrottled
and unchanged.

## Consequences

- The light leg works for the first time on hardware. `door-visiond`'s
  `esp32_profile_updates_acked` now counts relayed pushes, and door-api exposes
  `esp32_profile_relayed` / `_relay_failed` / `_relay_noop`, so a silent leg is visible on
  both sides — the failure mode §5.2 named.
- **It also removes a latent bug.** door-visiond previously allocated ESP32 sequence
  numbers from its own `_esp32_seq` counter that the transport knew nothing about; on the
  relay path it allocates no ESP32 seqs at all — door-api, the UART owner, does. The
  cross-counter hazard is gone in production.
- `Esp32Transport` gains `send_event` on the Protocol (it already existed on the concrete
  transport and was documented in the package README); the simulator's `FakeEsp32Transport`
  implements it too.
- **Best-effort, never critical path.** A missing/mock ESP32 is a recorded 202 no-op; an
  offline controller is a recorded failure. A hung or dead door-api costs a dropped push
  and a log line, never a recognition frame — the relay runs on door-visiond's own async
  task, exactly like the identity hop.
- **Deploy ordering.** door-api must expose `/internal/esp32/profile` before door-visiond
  starts relaying to it; an older door-api returns 404 and the push is dropped best-effort
  (the light degrades, nothing breaks). On the single-home door both are deployed together.
- ARCHITECTURE.md §5.2 is corrected to describe this path rather than a direct push.
