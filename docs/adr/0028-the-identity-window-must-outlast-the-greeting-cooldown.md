# ADR-0028: the held identity window must outlast the greeting cooldown

**Status:** Accepted · **Date:** 2026-08-18

Constrains [ADR-0020](0020-interaction-scoped-identity.md). No new state, no contract change.

## Context

The owner reported, repeatedly, that the door would greet them and then stop — "I tried
looking into the camera again just now and nothing happened."

Two independently reasonable constants produced it. door-visiond rate-limits greetings to one
`vision.identity_stable` per person per 30 s (`VISIOND_GREETING_COOLDOWN_MS`, P-10) so the
door does not say "Hi Tiger" every two seconds. door-api held a recognised name for 12 s of
idle (ADR-0020) so a name does not outlive the person by much.

Measured on the door, with someone standing there continuously, the events arrive at exactly
the cooldown floor:

```
20:21:36   20:22:06   20:22:36
```

So the hold expired at t+12 and the next announcement was not permitted until t+30. **For 18
of every 30 seconds the door had forgotten a person standing directly in front of it**, and
nothing the visitor could do would bring the name back, because the only signal that would
restore it was the one being suppressed. Looking at the camera again is precisely the action
that cannot work.

Neither constant was wrong alone. The relationship between them was never stated, so nothing
stopped them being set to values that guarantee a dead zone.

## Decision

**`DOOR_API_IDENTITY_IDLE_TTL_S` must be greater than or equal to
`VISIOND_GREETING_COOLDOWN_MS`.** The default moves from 12 s to 33 s — the cooldown plus a
small margin — and a test in `apps/door-api/tests/` fails if the two drift back into a gap.
The two services do not share a settings module, so the cooldown is restated in that test
deliberately: the point is to fail when they diverge, not to follow one silently.

The cooldown means "do not greet this person again yet". It was never meant to mean "door-api
may forget this person". With no separate liveness signal on the wire, the holder's window has
to cover the gap.

## Consequences

- A recognised name now lingers up to 33 s after someone leaves, rather than 12 s. That is
  the cost, and it is the right side to err on: the previous behaviour forgot people who were
  *present*, which is both more visible and more annoying than briefly remembering someone who
  has gone. The interaction window was already 120 s, so this is well inside the envelope
  ADR-0020 accepted.
- The window is still a departure detector, not a lease. A test caps it at 60 s so "cover the
  cooldown" cannot quietly grow into "hold the name indefinitely".
- The better fix, deliberately not taken tonight, is a cheap liveness signal — door-visiond
  already refreshes its own cache on *every* stable frame, independent of the greeting
  cooldown, and door-api simply cannot see it. Exposing that would let the idle window shrink
  back toward "the person actually left". That is a new event type and an ADR of its own.
