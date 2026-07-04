# API conventions (normative)

Applies to every HTTP/WebSocket service in the repo.

## Endpoints every service must expose

| Endpoint | Contract |
|---|---|
| `GET /health` | `{status: "ok"\|"degraded"\|"down", service, version, checks: {name: bool}}`, 200 unless `down` |
| `GET /metrics` | Prometheus text format; must include the service's latency-budget counters where applicable |

## HTTP

- JSON bodies validated by `packages/contracts` / Pydantic models; validation errors return 422 with field-level detail.
- Errors: `{error: {code: string, message: string, trace_id}}`. Never leak stack traces, paths, or internal hostnames — the door Pi is on a semi-trusted network.
- Public visitor endpoints (`/visitor` QR flows) use short-lived signed tokens, are rate-limited per IP + token, and accept only the minimal payloads defined in their briefs.
- Admin endpoints live under `/admin`, require authentication (session on Pi, token from `packages/auth`), and are never linked from public UI.
- Pagination: `?limit=&cursor=`; cursors opaque.

## WebSockets (Pi-local realtime)

- Single endpoint per service (`/ws`); messages are contract events (envelope included), JSON-encoded.
- Clients subscribe with `{"subscribe": ["session.*", "media.storage_status"]}`-style type filters.
- Server sends `{"type": "system.hello", ...}` with current snapshot state on connect so a reloaded kiosk recovers without history replay.
- Heartbeat ping/pong every 15 s; kiosk clients auto-reconnect with backoff and re-render from snapshot.

## Time, IDs, tracing

- UTC everywhere internally; the UI layer localizes for display only.
- Durations and expiries computed from monotonic clocks, never wall clocks.
- `trace_id` accepted via `X-Trace-Id` header, generated if absent, propagated to all downstream calls/events and every log line.
- IDs: UUIDv7 for events/entities; opaque short prefixes for human-adjacent IDs (`prs_`, `rec_`, `ses_`) are fine but never derived from personal data.

## Logging

Structured JSON via `packages/observability`: `{ts, level, service, msg, event_id?, trace_id?, session_id?, latency_ms?, error?}`. Never log raw frames, embeddings, tokens, or full user-generated content bodies at INFO level.
