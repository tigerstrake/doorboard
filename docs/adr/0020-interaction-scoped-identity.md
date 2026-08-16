# ADR-0020: A recognised identity lasts as long as the interaction

**Status:** Accepted · **Date:** 2026-08-16

Refines [ARCHITECTURE.md §5](../../ARCHITECTURE.md) (identity cache) and
[ADR-0018](0018-recognised-identity-personalisation.md) (personalisation). Supersedes
neither: the 2.5 s cache and the approach timer keep their current values and meanings.

## Context

Observed on the door: recognition worked, the wallboard greeted "Hi Tiger", and tapping
through to Check In then offered only *Check in as Guest* — with the explanation that
named check-in requires a recognised, consenting visitor. The door had recognised
someone and, seconds later, denied that it had.

Nothing was broken in the sense of a defect in one component. Two correct lifetimes were
being asked to serve a third purpose neither was built for:

- **door-visiond's `current_visitor`, 2.5 s** (ARCHITECTURE.md §5) exists so a bell press
  gets a personalised effect without paying recognition latency. It is deliberately
  shorter than a person's presence, and refreshes only while a face is being matched.
- **door-api's `approach_timeout_s`, 10 s** exists so an empty doorway returns to
  ambient. It measures "has anything happened", not "is anyone here".

Attribution and the greeting name both read the *session*, so both died with the approach
timer. Worse, `vision.identity_expired` fires whenever the 2.5 s cache lapses — which
happens constantly while somebody stands at the panel looking *down at it* rather than at
the camera. The person most likely to lose their identity was the one actively using the
door.

The visible symptoms were all one cause: no greeting by the time you looked up, no
accent colour, and a check-in screen that had forgotten you.

## Decision

**door-api holds a recognised identity for the length of the interaction, separately from
the session state machine.** `RecognisedIdentity` keeps `person_id`, display name,
consent version and profile, with two windows:

| Window | Default | Meaning |
|---|---|---|
| `idle_ttl_s` | 12 s | Recognised, nobody touched anything. Roughly the old behaviour. |
| `interaction_ttl_s` | 120 s | Re-armed by every doorpad/visitor request. |

**Activity-scoped rather than simply longer.** A passer-by who is recognised and walks on
is forgotten in twelve seconds; their name does not sit in memory for two minutes. Only
*using* the door extends the window, and only while it is still live — a touch cannot
resurrect an expired identity, or the first tap after a quiet spell would re-attribute the
door to whoever was last seen.

**The session machine owns the notification.** `handle_identity_stable` calls an identity
observer, so every path that reports an identity — the ESP32 consumer, the internal
ingest, the simulator, a test — updates both. Wiring it at one call site instead was the
first implementation, and it immediately produced five tests where the machine knew a
name and the holder did not. Two entry points for one fact is how a greeting and a
check-in button drift apart.

**`identity_expired` does not clear it.** That event means "a face left the frame", which
is not "a person left the door". Clearing on it would reinstate the bug.

**Privacy mode does clear it.** door-api now handles `vision.privacy_mode_changed` and
forgets the identity when recognition is switched off (ADR-0009 §4). Previously door-api
held nothing across states, so there was nothing to flush; now there is, and a privacy
flip that left a name on screen would be a privacy defect rather than a cosmetic one.

**Memory-only, deliberately.** The identity is not persisted. A restart forgets who is at
the door, because the person it names may have left — an identity restored from disk
would attribute a stranger's writes to them. Session state is still persisted as before.

**Personalisation, never authorisation.** Every consumer uses this for a greeting, an
accent colour, or a name on a voluntary write. ADR-0005 §3 and ADR-0009 P-11 apply
unchanged; nothing here reaches an access decision, and there is no lock on this door.

## Consequences

- A name and `person_id` live in door-api memory for up to two minutes of continuous
  interaction, where previously the ceiling was ten seconds. This is an identity
  reference, never an embedding, so ADR-0009's biometric rules are untouched — but it is
  a real extension of how long the door remembers someone, which is why it is written
  down rather than tuned in a config file.
- Writes made during an interaction are attributable for longer, which is the point: the
  visitor is told before each write (E-23), and the disclosure is now accurate for the
  whole interaction instead of expiring mid-flow.
- `IDLE` no longer implies "nobody is here" for display purposes. Surfaces must read
  `display_name` from the snapshot rather than inferring absence from the state; door-ui
  cleared it unconditionally on `IDLE`, which was half of the original bug.
- The doorpad sends a keepalive (`POST /doorpad/activity`, via `sendBeacon`) because its
  screen changes are client-side and reach no route. An idle panel cannot hold a name by
  polling, since a touch cannot revive an expired identity.
- Two more tunables exist. Both are bounded and both fail toward forgetting.
