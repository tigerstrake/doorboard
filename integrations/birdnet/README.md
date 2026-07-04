# integrations/birdnet — BirdNET-Go adapter

Adapter between the dedicated bird Pi 4 (running [BirdNET-Go](https://github.com/tphakala/birdnet-go) with a window USB mic) and the control plane. Task: T-601 (Gemini). Feature flag: `FEATURE_BIRDNET`.

- Pull/receive detections from BirdNET-Go's API; apply configurable confidence threshold and regional species filter.
- Summarize into `ambient.bird_summary` (top species, counts) — raw audio stays off by default and never leaves the bird Pi.
- Daily collage job hook (async, control plane).
- Interface: `BirdProvider` with `birdnet_go | mock` implementations; mock ships plausible fixture data.
- BirdNET-Go itself is deployed via `deploy/pi-bird/` — this package is only the adapter.
