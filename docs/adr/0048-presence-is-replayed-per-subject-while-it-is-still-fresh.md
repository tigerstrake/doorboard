# ADR-0048: presence is replayed per subject while it is still fresh

**Status:** Accepted · **Date:** 2026-09-24 · **Amends:** ADR-0027 (door-api replays the last ambient event) · **Relates to:** ADR-0035, ADR-0037

## Context

ADR-0027 made door-api remember the last `ambient.*` event per type and hand it to a kiosk on
connect, so a reload does not blank the birds/aircraft/satellite tiles until their next publish. It
deliberately excluded `status.*`: "a client connecting later would learn where someone was several
minutes ago".

Two audits showed what that exclusion costs and why the argument is weaker than stated:

- The presence board is the wallboard's primary tile, and it reads "Presence unavailable." after every
  kiosk relaunch, door-ui restart or reload until someone's status *changes* — hours, on a stable
  evening. The corridor sees an empty board for most of the day after any reboot.
- The label the late client would "learn" is the same broad label the same corridor was shown a moment
  earlier and will be shown again at the next change. The payload carries `until`, and the control plane
  has its own staleness contract (`CONTROL_PLANE_PRESENCE_STALE_AFTER_S`, 30 min), so a bound already
  exists for how long a label is meant to be believed.
- The control plane publishes all subjects on **one** retained MQTT topic, so after any restart only the
  last-published subject survives on the broker; the wallboard then also maps subjects to residents by
  arrival order, not by `subject_id`. Replaying "the last presence event" is therefore not even
  well-defined today.

## Decision

1. **The control plane publishes presence per subject.** `status.presence_changed` goes to
   `doorboard/status/presence_changed/<subject_id>` (retained). The un-suffixed topic is published too
   for one release so an older door-api keeps receiving it; the Home Assistant discovery config gets one
   sensor per configured subject.
2. **door-api remembers the last `status.*` event per `(type, subject_id)`** alongside its ambient
   cache (bounded to eight subjects) and replays it to a connecting client **only while it is fresh**:
   `now < occurred_at + CONTROL_PLANE_PRESENCE_STALE_AFTER_S` and, when the event carries `until`,
   `now < until`. A stale or expired label is not replayed; the tile shows *unknown*, not the past.
3. **The wallboard renders presence by `subject_id`, never by arrival order.** A fixed row per
   configured subject (`VITE_PRESENCE_SUBJECTS`, parallel to `VITE_RESIDENTS`) shows *unknown* until a
   fresh event arrives, and honours `until` locally so a time-bounded label expires on screen even if
   the resolver never re-publishes.

## Consequences

- ADR-0027's privacy stance is kept in spirit: nothing older than the control plane's own freshness
  bound is ever replayed, and expiry is honoured on both ends. What changes is that a fresh, current
  label survives a reload.
- ADR-0027's "presence deliberately not cached" note is superseded by this ADR; its ambient rules are
  unchanged.
- The retained-topic change is additive (old topic kept one release), so the NUC can be deployed before
  or after the door.
