# integrations/aircraft — nearby aircraft

Task: T-603 (Gemini). Feature flag: `FEATURE_AIRCRAFT`.

**MVP:** OpenSky REST API with a geographic bounding box around campus, aggressive caching, and rate-limit compliance (anonymous quotas are small — poll gently, cache hard). Output `ambient.aircraft_summary` with only fields the source actually provides (callsign, altitude, distance, heading) — do not fabricate route/destination data.

**Later (separate task, not MVP):** RTL-SDR + dump1090 on the spare Pi (`deploy/pi-adsb/`) feeding the same interface locally.

Interface: `AircraftProvider` with `opensky | dump1090 | mock` implementations. Credentials NUC-only.
