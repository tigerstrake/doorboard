# ADR-0035: presence needs an invitation and a middle ground

**Status:** Accepted · **Date:** 2026-08-22

## Context

`PresenceLabel` had seven usable values: `available`, `busy`, `do_not_disturb`, `sleeping`,
`at_class`, `at_library`, `away`.

Two gaps showed up once the door was actually on a dorm corridor.

**There is no way to invite anyone.** Every label is a flavour of *unavailable*, and `available`
is passive — it means "I exist", not "come in". The one signal that makes a door display social
rather than an elaborate do-not-disturb sign was missing.

**`busy` is ambiguous in exactly the wrong way.** A visitor cannot tell whether it means "don't
knock" or "knock anyway". `do_not_disturb` unambiguously means don't. Nothing said "knock if it
matters" — which, on a corridor of students, is the most common real state: working, but you
would surface for something that mattered.

A third idea was considered and rejected: a separate "locked in" label. It is a near-synonym of
`do_not_disturb` and `busy`, and three overlapping don't-bother-me states make the signal *less*
readable, not more — someone at the door has to decide what "Locked In" means versus "DND"
versus "Busy", and they will guess. Near-synonyms are how a status board stops being trusted.

## Decision

Add two labels:

- **`social`**, displayed **"Come In"** — an active invitation, distinct from the passive
  `available`.
- **`knock_if_urgent`**, displayed **"Knock if urgent"** — the middle ground between `busy` and
  `do_not_disturb`.

Retext `do_not_disturb` to **"Locked In"** rather than adding a label for it. That is the
household's actual vocabulary, and it costs no contract change and no deployment ordering. The
tradeoff is deliberate: "DND" reads to any stranger, "Locked In" reads to dorm-mates, and the
visitors here are dorm-mates.

The result is a scale that reads at a glance rather than seven overlapping states:

> Come In → Available → Knock if urgent → Locked In → Sleeping

plus the orthogonal where-am-I labels (`at_class`, `at_library`, `away`).

### Rejected: health and guest states

`sick` and `guest_over` were both considered and rejected on privacy grounds, and are recorded
here because they are the obvious next suggestions. `sick` puts health information on a
corridor-facing display. `guest_over` discloses a second person's presence to everyone who walks
past, and that person never agreed to it. Neither belongs on a shared-hallway screen.

### Deployment ordering

Per [ADR-0031](0031-additive-contract-fields-are-not-backward-compatible.md), this is additive to
the schema but **not** backward compatible at runtime. A new *enum value* fails validation on a
consumer built before it, exactly as a new field does: the consumer rejects the event rather than
ignoring the unknown value.

door-api and door-visiond publish upward through `/ingest`, and control-plane-api is what
validates. So: **deploy the NUC before the door.** A door emitting `social` at an older NUC gets
the presence event rejected at ingest.

Nothing here is on the door's critical path. Presence remains display-only —
ARCHITECTURE.md §9, personalization is never authorization — so an unrecognised label degrades to
a stale tile, never to a locked or unlocked door.

## Consequences

- `labelDisplayNames` and `labelIcons` in `packages/ui-kit/src/StatusBadge.tsx` are both
  `Record<PresenceLabel, …>`, so the compiler refuses a half-added label. That is the intended
  safety net and the reason no runtime fallback was added for these two.
- Two more CSS custom properties and badge rules, keeping the colour ramp legible:
  lime (`social`) → teal (`available`) → amber (`busy`) → orange (`knock_if_urgent`) →
  red (`do_not_disturb`) → violet (`sleeping`).
- Ten labels is close to the limit of what a glanceable badge can carry. Further additions should
  replace rather than extend.
