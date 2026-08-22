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

Three labels are **retexted** rather than added, because display copy costs no contract change
and no deployment ordering:

| Value | Displays as | Why |
| --- | --- | --- |
| `do_not_disturb` | **Locked In** | The household's own vocabulary. "DND" reads to a stranger; "Locked In" reads to a dorm-mate, and the visitors here are dorm-mates. |
| `busy` | **Working** | `busy` is the calendar-driven label — the source that expires "busy until 15:00" on schedule — so a calendar event reading "Working" is exactly right. |
| `sleeping` | **Recovery** | Covers a nap, being ill, or just being done for the day, without any of them having to be stated on a corridor-facing screen. |

The settled scale is four points, not five:

> Come In → Working → Locked In → Recovery

plus the orthogonal where-am-I labels (`at_class`, `at_library`, `away`).

`knock_if_urgent` was added in the first pass and is **retained but unused**: "Working" absorbed
its meaning (working, so knock if it matters), which made a fifth point redundant. It is not
removed because `presence_history` already holds a row carrying that value and
`PresenceLabel(row.label)` raises on an unknown one — deleting the enum member would break the
history endpoint for the sake of tidiness. `available` likewise stays as the neutral resting
state the `default` source falls back to.

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
