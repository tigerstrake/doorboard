# integrations/satellites — ISS/satellite pass prediction

Skyfield-based visible-pass calculation on the NUC. Task: T-602 (Gemini). Feature flag: `FEATURE_SATELLITES`.

- Scheduled TLE refresh from CelesTrak (cached locally; respect their guidance on fetch frequency; treat stale TLEs > 7 days as degraded).
- Visible-pass computation: observer coordinates (NUC-only secret-adjacent config — never displayed), minimum elevation, observer darkness, satellite illumination.
- Output: the next *selected* pass (ISS prioritized, configurable watchlist) as `ambient.satellite_pass` — not a dump of every pass.
- Interface: `SatelliteProvider` with `skyfield | mock` implementations; unit tests use pinned TLE fixtures so results are deterministic.
- NUC calculates, Pi renders — no Skyfield on the door Pi.
