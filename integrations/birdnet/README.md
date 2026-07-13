# integrations/birdnet — bird summary adapters

This package is the typed adapter boundary between a dedicated bird Pi and the
NUC-hosted `wallboard-worker`. It never runs on the door Pi and never reads or
transports audio. Task: T-601. Feature flag: `FEATURE_BIRDNET`.

## Providers

- `avian_visitors` (production default): polls
  `GET /avian/api/birdnet-api.php?action=recent&hours=N` on the dedicated Pi 4.
  Responses are streamed into a bounded buffer, schema-validated, filtered by
  confidence/species, and deterministically sorted. Optional HTTP Basic Auth is
  supported. The native endpoint is already species-collapsed: `n` becomes the
  species count and upstream `best_conf` is placed in the existing contract's
  `confidence_avg` field. It is a best confidence, not a mathematical average;
  the event contract is intentionally unchanged.
- `birdnet_go` (legacy): polls BirdNET-Go's v2 detections endpoint and computes
  per-species counts and average confidence for today's detections.
- `mock`: deterministic fixture data for laptop development and CI.

The AvianVisitors rolling window is configured by
`AVIAN_VISITORS_RECENT_HOURS` (default 24). The existing
`ambient.bird_summary` contract still carries `window="today"`; no new field is
invented. Consumers should treat `occurred_at` as the summary observation time.

## Filtering and degraded behavior

`BIRDNET_CONFIDENCE_THRESHOLD` applies after retrieval.
`BIRDNET_SPECIES_FILTER` is a comma-separated allowlist; AvianVisitors matches
both common and scientific names case-insensitively.

An unreachable Pi, timeout, non-2xx status, redirect, oversized body, invalid
JSON, invalid counts/confidence/timestamps, duplicate species, or mismatched
window raises a provider error. The bird job catches it and posts no replacement
event, so the control plane retains the last bird summary and the tile follows
its stale-data path. The scheduler continues running every other enabled job.

Connection and provider variables are documented with the complete worker
configuration in [apps/wallboard-worker/README.md](../../apps/wallboard-worker/README.md).
Bird Pi installation and network isolation are documented in
[deploy/pi-bird/README.md](../../deploy/pi-bird/README.md).
