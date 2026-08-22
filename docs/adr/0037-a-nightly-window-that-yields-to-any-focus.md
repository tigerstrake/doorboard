# ADR-0037: a nightly window that yields to any Focus

**Status:** Accepted · **Date:** 2026-08-22

## Context

The owner wanted the door to read **Recovery** from 23:00 to 07:00 every day, *except* when
another Focus is on — and to go **back** to Recovery the moment that Focus is turned off.

The obvious cheap answer was to schedule an iOS Focus and let the phone drive it, since iOS Focus
modes are mutually exclusive. That was rejected by the owner for a concrete reason: the real iOS
Sleep Focus is wanted for actually sleeping, with its alarms and wind-down, and it should *not* be
the thing that drives a door status. Nor should a second, decoy Sleep Focus have to exist just to
feed a display.

So the window belongs on the door side. Which surfaces the interesting clause: "back to Recovery
when I turn the other Focus off" is a *fallthrough* requirement, not an event.

## Decision

A new presence source, `schedule`, placed **below** `calendar`:

> manual > focus_shortcut > geofence_label > calendar > **schedule** > default

It is the weakest real signal on purpose. It encodes a standing assumption about a time of day,
so anything that actually knows something — a deliberate Focus, a location, a class — outranks it.
`away` at 2am is more useful than `sleeping`, because the door can see you are not there.

**It is computed live, not stored** — like `calendar`, and for the same reason. The label is
derived from the clock on every resolution, so when a higher source is cleared the schedule
reappears immediately, with nothing needing to re-fire. A stored row would have to be
re-triggered, and could get stuck reading Recovery at noon. This is what makes the owner's
fallthrough clause work at all.

`until` is set to the end of the window, so the existing expiry machinery — the same one that
reverts "busy until 15:00" — drops it at 07:00 with no background task.

`PRESENCE_SCHEDULE_SUBJECTS` scopes it. Blank means every subject, but one person's sleep habit
should not be imposed on whoever shares the door.

### The webhook has to be able to clear

This is the part without which the feature silently does not work.

Precedence falls through on *absent* entries, not on "uninteresting" ones. A Focus-off shortcut
that writes `available` puts a **real value** in `focus_shortcut`, which outranks `schedule` — and
the door then sits on Available all night, exactly the opposite of what was asked for.

So `PresenceWebhookRequest.label` becomes `PresenceLabel | None`, where `null` **clears** the
source. It stays **required**: a nullable-but-required field means a client has to say what it
means, whereas an optional one would let a client that forgot the field silently wipe presence.

A test pins the failure mode itself — that `available` in `focus_shortcut` beats the schedule — so
that nobody later "simplifies" the clear away without seeing what it costs.

### Distinguishing the two kinds of night

The owner keeps iOS Sleep Focus for real sleep. That writes `focus_shortcut`, so it outranks the
schedule. Both resolve to the same *label*, but the reported **source** differs — `focus_shortcut`
versus `schedule` — which is what lets "actually asleep" be told apart from "it is the middle of
the night and nothing else is known".

## Consequences

- One more entry in `SOURCE_PRECEDENCE` and in `INFERRED_SOURCES`, so it is gated by a subject's
  `tracking_enabled` flag like the other inferred sources.
- Not in `STORED_VALUE_SOURCES`, so `_require_stored_source` already rejects any attempt to POST a
  value to it. The window is configuration, not something a client can set.
- `schedule_provider` threads through the resolution chain as an **optional** parameter defaulting
  to `None`, so existing callers and tests are unaffected and "unconfigured" is the safe default.
- The window-parsing logic now exists twice: here, and as quiet hours in `door-voice`
  (ADR-0034). Deliberate duplication — they are separate deployables, and sharing it would mean a
  new package for two dozen lines.
- A malformed window raises at startup rather than disabling silently. The safe failure for "is
  the door meant to say Recovery at night" is a loud one.
