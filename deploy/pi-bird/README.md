# deploy/pi-bird — BirdNET-Go node

Dedicated Pi 4 near a window with a USB microphone, running BirdNET-Go — deliberately isolated from the door plane (handoff §13/§14).

- BirdNET-Go install (container or binary), configured with local coordinates, confidence threshold, regional filter.
- Raw audio retention off by default; only detection summaries leave the device (pulled by `integrations/birdnet`).
- Nothing else runs here; the door system must not notice if this Pi disappears.
