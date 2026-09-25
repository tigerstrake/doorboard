# packages/config — configuration loading

> **Status of the config bundle (audit H1, 2026-09):** no Pi service consumes the bundle
> today. door-api, door-visiond, door-media and door-sync each read their own environment
> at startup; none fetches `GET /config/door/{door_id}`, so `DoorConfigSettings`
> (`vision_mode`, `single_camera_mode`, `esp32_transport`, `identity_cache_ttl_s`,
> `feature_flags`) and the `config` token scope have no effect on the door. The package is
> a producer without a consumer. Whether to wire a Pi-side puller (door-api on startup,
> then fan out) or remove the route, scope and package behind a short ADR is an **open
> owner decision**; until it is made, do not build on the bundle as if the door read it.

Typed configuration for all Python services (T-000 establishes it; each service extends it).

- Pydantic-settings models: env vars per `.env.example`, with per-service sections and validation at startup (fail fast, clear message).
- Feature flags (`FEATURE_*`, `VISION_MODE`, `ESP32_TRANSPORT`) resolved here so services branch on typed config, not raw env reads. (`SINGLE_CAMERA_MODE` was listed here but nothing reads it; it is gone from both env templates.)
- Secrets discipline: this package never logs values, only key names; secret-bearing fields are `SecretStr`.
- Versioned config bundles (implemented, T-501): `doorboard_config.bundle` defines `DoorConfigSettings` (the non-secret, `extra="forbid"` whitelist of what the Pi may receive) and `ConfigBundle` (`door_id`, `version`, `checksum`, `settings`). `build_bundle`/`verify_checksum` compute/check a SHA-256 over canonical JSON; `assert_secret_free` denylist-scans a bundle's serialized form so a credential can never ship even by accident. `control-plane-api` owns bundle storage/versioning and serves it from `GET /config/door/{door_id}`.
- No global mutable state; services construct config once at startup and inject it.
