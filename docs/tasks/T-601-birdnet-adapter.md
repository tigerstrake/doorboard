# T-601: BirdNET adapter + bird Pi deployment

**Agent:** gemini · **Milestone:** M6 · **Depends on:** T-501, T-503

## Context

Specs: [integrations/birdnet/README.md](../../integrations/birdnet/README.md), [deploy/pi-bird/README.md](../../deploy/pi-bird/README.md). Ambient tile exists (T-004); this makes its data real.

## Deliverables

- `BirdProvider` interface + `birdnet_go` implementation (poll BirdNET-Go API) + `mock` with fixture data; confidence threshold and regional species filter in typed config.
- wallboard-worker job: summarize → `ambient.bird_summary` on a schedule; daily-collage job stub (image grid from detection thumbnails if BirdNET-Go exposes them, else species list card).
- `deploy/pi-bird/`: install/config procedure for BirdNET-Go on the Pi 4 (pinned version, local coordinates, raw-audio retention off), USB mic notes.
- Tests against recorded API fixtures; failure → tile stale-marker path verified.

## Out of scope

Door Pi anything, raw audio handling, changing tile design.

## Acceptance criteria

- Mock-mode end-to-end: worker → control-plane → wallboard tile shows fixture birds.
- Against a real/recorded BirdNET-Go instance: filtered summary matches expected fixture output.
- Bird Pi unreachable → tile shows last data + staleness, no errors elsewhere (simulator scenario).
