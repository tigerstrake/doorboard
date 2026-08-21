# ADR-0021: An accent colour is not an LED profile

**Status:** Accepted · **Date:** 2026-08-17

Refines [ADR-0009](0009-enrollment-and-biometric-data.md) §1 (`profile_id` uniqueness) and
extends [ADR-0020](0020-interaction-scoped-identity.md) (the identity that carries it).
Does not change what recognition is for: personalisation, never authorisation.

## Context

The owner asked for a colour picker at enrollment, with precise control, and for two
people to be allowed the same colour. Today neither is possible, because one field is
doing two unrelated jobs.

`profile.profile_id` names an **LED effect in the ESP32's firmware** — `warm_amber`,
`blue_wave`, and four others from T-103's effects catalogue. The controller can only play
an effect it was flashed with, so this genuinely must come from a fixed list, and ADR-0009
§1 makes it `UNIQUE` so each person's door light is distinguishable.

`profile.color` is then *derived from the catalogue entry that got assigned*. That has two
consequences nobody chose:

- **A taken profile silently changes your colour.** `_allocate_profile_locked` hands the
  second person to ask for `warm_amber` the next free catalogue entry — correct for the
  LED, since two identical lights defeat the point — and takes its colour along with it.
  The enrollee picked amber and the screens show them violet.
- **There are exactly six colours, ever.** A seventh enrollee cannot be enrolled at all:
  `NoProfileAvailableError`. That is a reasonable limit on distinguishable door lights and
  an absurd one on a hex colour.

The two constraints are unrelated. The firmware's is real and narrow; the screen's is
imaginary.

## Decision

**Split them.** A person has both:

| | Purpose | Constraint |
|---|---|---|
| `profile_id` | The ESP32 LED effect | From the catalogue, `UNIQUE`, reassigned when taken (unchanged) |
| `accent_color` | Every screen: greeting, badge, doorpad frame | Any valid hex, **not unique**, never reassigned |

**The colour is what the enrollee chose, always.** Reassigning a profile no longer touches
it. Two people may both be `#ffb300`; the door tells them apart by name and by LED, which
is what those are for.

**The catalogue keeps its colours as *defaults*.** Picking `warm_amber` with no explicit
colour still gets `#ffb300`, so nothing about the existing flow changes for someone who
does not care. Existing rows are migrated to their catalogue colour, which is exactly what
they display today — the migration is a no-op visually, by design.

**It travels on `vision.identity_stable`.** The kiosks learn a recognised person's colour
the same way they learn their name. This is a change to the normative event catalogue and
therefore needs this ADR (ADR-0003); the field is a display string, carries no biometric
content, and is covered by the same consent as the name it arrives beside.

The alternative — a public endpoint mapping `person_id` to colour — was rejected: it puts
an identity-keyed lookup on a route the wallboard can reach, to save a string on an event
that already carries the person's display name.

**The mirrored TypeScript catalogue stays.** `packages/ui-kit`'s table remains the
fallback for a `profile_id` arriving without a colour (an older door, a pre-migration
row), and its drift test still applies. It is no longer the source of what a person's
colour *is*.

**A seventh person can now enroll.** With colour decoupled, exhausting the LED catalogue
is no longer a hard stop on enrollment — it is a door where the seventh person shares an
LED effect. That is a strictly better failure than refusing to enrol them. `profile_id`
stays `UNIQUE` until the catalogue is full, then repeats; the greeting, the name and the
colour remain per-person regardless.

## Consequences

- One more column, one more contract field, and a migration that changes nothing on
  screen the day it runs.
- Two people can now be visually identical on the wallboard. That is the owner's explicit
  request; the name is what disambiguates, and it is always shown beside the colour.
- Colour is now attacker-chosen input rendered as CSS. It is validated as a strict hex
  literal at the boundary and never interpolated as a style string, because "the enrollee
  picks the colour" and "the enrollee picks arbitrary CSS" are one bug apart.
- The ESP32 protocol is untouched: it still receives only an opaque `profile_id` and a
  monotonic expiry, and still holds no colour of its own.
