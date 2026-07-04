# T-103: LED/audio effects + profile table

**Agent:** gemini · **Milestone:** M1 · **Depends on:** T-101
**Why this agent:** enumerable, pattern-following work against a fixed firmware API.

## Context

T-101 established the effects task and a placeholder table. This task fills the pre-flashed effects library: LED animations (WS2812B ring) and short audio cues, keyed by `effect_id`/`profile_id` (the wire never carries animation data — ADR-0006 / firmware README).

## Deliverables

- Effects table module in firmware: `generic_press`, `fallback` (Pi-offline), `boot`, `privacy_mode`, `error_admin`, plus **six** distinct personalized profile effects (e.g. `blue_wave`, `sunrise`, `mint_pulse`, …) each with an LED animation and optional short audio cue.
- Animation micro-framework if T-101 didn't finish one: non-blocking, tick-driven from the effects task, no `delay()`-style busy waits, interruptible by a new trigger.
- Audio cues as compiled-in samples or tone sequences (document size budget; stay within flash comfortably).
- A `docs/hardware/effects-catalog.md` table: effect_id, description, duration, LED pattern, audio, intended use.
- Host-side tests for the animation state machine (tick sequences → expected frame buffers) where the module is pure.

## Out of scope

Wire protocol, button/link logic, new hardware, changing effect *semantics* (when effects trigger is Pi/firmware-core logic).

## Acceptance criteria

- All effects render on bench hardware (video attached to PR is fine as evidence); no effect blocks the input task (button during animation still gives immediate generic feedback — verified on bench).
- Effects catalog doc complete and matching code identifiers exactly.
- Firmware still builds with pinned IDF; flash/RAM usage report included in PR description.
