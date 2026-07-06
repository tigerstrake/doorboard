# packages/config — configuration loading

Typed configuration for all Python services (T-000 establishes it; each service extends it).

- Pydantic-settings models: env vars per `.env.example`, with per-service sections and validation at startup (fail fast, clear message).
- Feature flags (`FEATURE_*`, `VISION_MODE`, `SINGLE_CAMERA_MODE`, `ESP32_TRANSPORT`) resolved here so services branch on typed config, not raw env reads.
- Secrets discipline: this package never logs values, only key names; secret-bearing fields are `SecretStr`.
- Versioned config bundles (implemented, T-501): `doorboard_config.bundle` defines `DoorConfigSettings` (the non-secret, `extra="forbid"` whitelist of what the Pi may receive) and `ConfigBundle` (`door_id`, `version`, `checksum`, `settings`). `build_bundle`/`verify_checksum` compute/check a SHA-256 over canonical JSON; `assert_secret_free` denylist-scans a bundle's serialized form so a credential can never ship even by accident. `control-plane-api` owns bundle storage/versioning and serves it from `GET /config/door/{door_id}`.
- No global mutable state; services construct config once at startup and inject it.
