# packages/config — configuration loading

Typed configuration for all Python services (T-000 establishes it; each service extends it).

- Pydantic-settings models: env vars per `.env.example`, with per-service sections and validation at startup (fail fast, clear message).
- Feature flags (`FEATURE_*`, `VISION_MODE`, `SINGLE_CAMERA_MODE`, `ESP32_TRANSPORT`) resolved here so services branch on typed config, not raw env reads.
- Secrets discipline: this package never logs values, only key names; secret-bearing fields are `SecretStr`.
- Versioned config bundles: helpers to load/verify the config bundle distributed by control-plane-api to the Pi (version, checksum, signature slot for later).
- No global mutable state; services construct config once at startup and inject it.
