# packages/observability — logging, metrics, tracing

Shared telemetry for all Python services (T-000 establishes it).

- **Logging:** structured JSON per [api-conventions.md](../../docs/protocols/api-conventions.md) — `ts, level, service, msg, event_id?, trace_id?, session_id?, latency_ms?, error?`. OpenTelemetry-compatible field names. Redaction guard: raw frames, embeddings, tokens, and full UGC bodies are never loggable (typed helpers refuse them).
- **Metrics:** Prometheus registry + FastAPI `/metrics` mount; histogram helpers pre-declared for every latency-budget path in ARCHITECTURE.md §4 so services measure consistently (`system.latency_sample` emission included).
- **Tracing:** `trace_id` context propagation (contextvars) across HTTP calls, WebSocket messages, and emitted events; `X-Trace-Id` middleware.
- **Health:** standard `/health` route factory with named sub-checks (`hailo_ok`, `ssd_mounted`, `esp32_link`, …).
- Monotonic-clock timing utilities — wall-clock duration math is a review-blocking defect.
