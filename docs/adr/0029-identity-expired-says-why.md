# ADR-0029: `vision.identity_expired` says why

**Status:** Accepted · **Date:** 2026-08-18

Extends `vision.identity_expired`. Additive and optional; no existing field changes meaning.
Resolves a conflict between [ADR-0020](0020-interaction-scoped-identity.md) and the deletion
invariants in ARCHITECTURE.md §9 / [ADR-0009](0009-enrollment-and-biometric-data.md).

## Context

`VisionIdentityExpiredPayload` carried a single field, `person_id`. door-visiond emits the
event for three quite different reasons and already knew which was which — `_emit_expired`
takes a `reason` of `expired`, `admin` or `privacy_mode` — and dropped it on the floor when
constructing the event.

That collapsed two opposite facts into one indistinguishable message:

- **the vision cache lapsed** (`expired`): a face left the frame. Routine — it happens
  constantly while someone stands at the doorpad looking down at it. ADR-0020 exists
  *because* clearing the held name here made the greeting flicker off mid-visit, so door-api
  must ignore it.
- **the person was unenrolled** (`admin`), or **recognition was switched off**
  (`privacy_mode`): their face data has already been destroyed, and their name must come off
  the screen immediately.

Because both looked the same, door-api ignored both. So unenrolling somebody left their name
displayed on the door until an unrelated timer lapsed — up to 33 s of idle after ADR-0028, or
two full minutes if the doorpad was being touched. The About screen tells visitors, in as many
words, that removal "destroys the face data immediately rather than marking it hidden". The
data, yes. The name on the screen, no.

## Decision

**The payload carries `reason`.** `Literal["expired", "admin", "privacy_mode"] | None`,
optional so an older producer still validates, populated by door-visiond from the value it
already had.

**Consumers clear on the deletion reasons and only those.** door-api drops the held identity
for `admin` and `privacy_mode`, and continues to ignore `expired`.

**A missing reason means the routine case.** An older door sends no field, and the safe
default is the one that keeps the name: the deletion paths are the ones that clear
explicitly, so treating unknown as "cache lapsed" fails toward a stale greeting rather than
toward a flickering one. It cannot fail toward *retaining* a deleted person's name, because
deletion always sends its reason.

## Consequences

- `forget_person` is now reachable from the event path rather than only from privacy mode,
  which is what makes unenrollment propagate to the kiosks in the same beat as the delete.
- The reason names *why the vision layer let go*, not a general-purpose audit field. Adding
  reasons is cheap; consumers must keep defaulting unknown values to the routine branch.
- This narrows ADR-0020 rather than reversing it: the held identity still survives a face
  leaving the frame, which was the entire point of that decision.
