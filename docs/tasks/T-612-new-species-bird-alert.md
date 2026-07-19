# T-612: New-species bird alert

**Agent:** claude · **Milestone:** M7 · **Depends on:** T-601, T-610

Sends the owner a message the first time a detected bird isn't on their
known-species list (e.g. a bird outside the bundled illustration set).

## Context

The birds tile (T-601) shows detected species via `ambient.bird_summary`, and
T-610 added a Telegram/ntfy owner-notification channel. The owner wants to be
pinged when a bird shows up that isn't on "the list" — i.e. an unfamiliar
species (which is also the signal that its collage illustration may be missing).
This is a pure filter over the bird summary the control plane already ingests,
mirroring the aircraft-proximity rule (T-610); no contract or schema change.

## Deliverables

- An `ambient.bird_summary` rule in `control_plane_api.notify.evaluate_rules`:
  fires for the first detected species whose name isn't in `BIRD_KNOWN_SPECIES`
  (case-insensitive), with a per-species `rule_key` + a long
  `BIRD_NEW_SPECIES_COOLDOWN_S`, so each new species pings once, never the
  regulars. Off unless `BIRD_NEW_SPECIES_ALERT=true`.
- Wiring through `NotifyEngine` + settings + `.env.example` (seeded with the
  common species near 94305), delivered via the existing ntfy/Telegram channels.
- Unit tests (fires for unknown, silent for known, disabled default, case-insensitive).

## Out of scope

- Any `packages/contracts` change (uses the existing `ambient.bird_summary`).
- A BirdNET-Pi stats provider — doorboard's adapter is BirdNET-Go (T-601). The
  alert fires once bird detections reach the control plane; wiring an
  AvianVisitors/BirdNET-Pi feed is a separate task.
- Auto-generating the missing illustration (an AvianVisitors-side concern).

## Acceptance criteria

- A `bird_summary` naming a species not in `BIRD_KNOWN_SPECIES` produces one
  notification identifying it; a summary of only known species produces none.
- Disabled by default (`BIRD_NEW_SPECIES_ALERT=false`); matching is
  case-insensitive; the same new species doesn't re-alert within the cooldown.
- Delivered to whichever owner channel is configured (ntfy and/or Telegram).
- `scripts/lint`, `scripts/typecheck`, and `scripts/test` pass.
