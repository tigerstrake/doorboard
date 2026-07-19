# T-611: Bird collage in the wallboard birds tile

**Agent:** claude · **Milestone:** M7 · **Depends on:** T-601

Embeds an external live bird collage (e.g. an AvianVisitors frame PNG served by
a window bird-Pi) inside the wallboard birds tile, beneath the existing stats.

## Context

The birds tile (T-601) shows detection stats (species, counts, freshness) fed by
`ambient.bird_summary`, but no imagery. The owner wants the collage that
[AvianVisitors](https://github.com/Twarner491/AvianVisitors) already renders (its
`shoot.py`/frame PNG) shown on the door wallboard's birds tile — stats above, the
collage below — rather than on a separate screen. AvianVisitors produces the
collage as a PNG (screenshot of BirdNET-Pi, or a served `frame.png`), so the
cleanest bridge is to embed that image by URL; the bird-Pi serves it on the LAN.

## Deliverables

- `door-ui` birds tile (grid + focused view) renders an embedded collage `<img>`
  below the stats, from a new build-time `VITE_BIRD_COLLAGE_URL`. Graceful:
  hidden when unset and hidden on image load error; stats still render if the
  collage is absent, and the collage still renders if the stats feed is down.
- `.env.example`: `VITE_BIRD_COLLAGE_URL` documented (point at the bird-Pi PNG).
- Styling (`.bird-collage`) and a `birds.test.tsx` render test.

## Out of scope

- Any `packages/contracts` change (uses the existing `ambient.bird_summary`).
- A BirdNET-Pi stats provider — doorboard's adapter is BirdNET-Go (T-601); if the
  window Pi runs AvianVisitors/BirdNET-Pi, wiring its stats is a separate task.
  The collage embed is backend-agnostic.
- Generating/bundling illustrations (an AvianVisitors setup concern; the Western-US
  set ships there, and missing species use its `generate_illustrations.py`).
- Serving the collage PNG from the bird-Pi (operator setup).

## Acceptance criteria

- With `VITE_BIRD_COLLAGE_URL` set, the birds tile shows the collage below the
  stats; with it unset, the tile is unchanged (no broken image).
- Stats and collage are independent: either can render without the other.
- `scripts/lint`, `scripts/typecheck`, and `scripts/test` pass (door-ui vitest
  covers the tile).
