# ADR-0034: saying a name out loud is a louder disclosure than showing it

**Status:** Accepted · **Date:** 2026-08-21

## Context

The owner asked for the door to say "Hi &lt;name&gt;" when it recognises someone. The visual
version of this already exists: `shouldGreetOnApproach` shows a held identity's display name on
the doorpad.

Those two are not the same disclosure, and that is the whole reason this ADR exists.

The doorpad is a 7" screen at arm's length. The person being greeted reads it; a passer-by four
metres down the corridor does not. A speaker inverts that — **everyone within earshot learns who
is standing at this door**, including people the visitor never chose to tell. The door faces a
shared dorm corridor.

The About screen currently tells visitors that being enrolled "changes a greeting and a colour".
That sentence is true of a screen. It is not true of a speaker, so shipping audio on the existing
consent would make an accurate disclosure into a misleading one.

Separately, ARCHITECTURE.md §9 holds that personalization is never authorization. Audio does not
change that — but it does widen the audience for a personalization signal, which is a privacy
property in its own right.

## Decision

Speech ships, in a separate `door-voice` service, with four constraints.

**1. It lives outside the door's critical path.** `door-voice` is its own process, consuming
door-api's existing SSE broadcast. The instant chime stays on the ESP32 (ADR-0002 / CLAUDE.md
§1): if `door-voice` is slow, wedged, or dead, the bell still rings and the UI still responds.
Nothing on the `button → ESP32 feedback → local UI` path gains a dependency, and no TTS process
is ever spawned by the service that owns the button.

That also settles the option of reusing the ESP32's speaker: it would put speech synthesis
behind the microcontroller that owns instant feedback. Rejected.

**2. Default off, per-person opt-in.** `FEATURE_VOICE_GREETING` defaults to false. A recognised
person is greeted aloud only if their id is in `VOICE_GREETING_ALLOW`. Enrolment consent covers
the screen, not the corridor — so audio is opt-in per person rather than inherited, and an
enrolled person who never opts in is never named aloud.

An **allow**-list, not a deny-list: a deny-list means the default for a newly enrolled person is
"announced", and the failure mode of forgetting to add someone is a disclosure you didn't intend.

**3. Quiet hours.** `VOICE_GREETING_QUIET_HOURS` (default `22:00-08:00`, local time) suppresses
speech entirely. A door that announces visitors at 3am is a problem for roommates and neighbours
regardless of consent.

**4. On-device synthesis only.** Text goes to a local TTS binary (`piper`, falling back to
`espeak-ng`) and never leaves the Pi. A cloud TTS call would put a recognised person's *name* on
a third-party service on every approach — the clearest possible violation of the privacy
invariants — and would add a network round trip to a door interaction.

The per-person cooldown is deliberately longer than the visual greeting's
(`VISIOND_GREETING_COOLDOWN_MS=30000`): re-showing a name costs nothing, whereas re-announcing it
every 30 seconds at someone standing in their own doorway is both annoying and a repeated
disclosure.

## Consequences

- Needs a USB audio dongle; the Pi has only HDMI audio and the 7" touchscreen has no speakers.
  Until one is plugged in this service is inert, which is also its default state.
- The About screen must be updated **before** this is enabled for anyone, so the "greeting and a
  colour" sentence stops being the whole story. Tracked with the feature, not after it.
- Two greeting paths now exist with different cooldowns and different gating. They are
  deliberately not unified: they have different audiences.
- An opt-in list keyed on `person_id` is one more place a person's data lives, so it is covered
  by the ADR-0009 purge like any other reference.
