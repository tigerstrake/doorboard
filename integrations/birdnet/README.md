# integrations/birdnet — BirdNET-Go adapter

Adapter between the dedicated bird Pi 4 (running [BirdNET-Go](https://github.com/tphakala/birdnet-go) with a window USB mic) and the control plane. Task: T-601 (Gemini). Feature flag: `FEATURE_BIRDNET`.

- Pull/receive detections from BirdNET-Go's API; apply configurable confidence threshold and regional species filter.
- Summarize into `ambient.bird_summary` (top species, counts) — raw audio stays off by default and never leaves the bird Pi.
- "Today" is the local calendar day: the worker's `TZ` (or `BirdnetConfig.tz`) decides which date is requested and which detections count, and BirdNET-Go's naive `date`/`time` stamps are read in that zone (its RFC3339 `timestamp` is preferred when present). UTC internally, local at this boundary — evening detections used to vanish for the hours the UTC date had already rolled over.
- Daily collage job hook (async, control plane).
- Interface: `BirdProvider` with `birdnet_go | mock` implementations; mock ships plausible fixture data.
- BirdNET-Go itself is deployed via `deploy/pi-bird/` — this package is only the adapter.
