# tools/diagnostics

**This directory holds no code.** The health-sweep / ESP32 / Hailo / SSD / sync-queue
diagnostics tool that an earlier version of this file promised was never built, and the
`/diagnostics` route in door-ui renders the admin panel (`/admin`), not a diagnostics report.

What actually exists for operator diagnostics lives elsewhere:

| Need | Where it is |
|---|---|
| Per-service liveness and the §4 latency histograms | every service's `GET /health` and `GET /metrics` (loopback only) |
| Restart a Pi service that stops answering `/health` | [`scripts/doorboard-health-agent`](../../scripts/doorboard-health-agent) (3 strikes → `systemctl try-restart`) |
| Is the SSD really mounted and writable before services start | [`scripts/check-door-storage`](../../scripts/check-door-storage) |
| Can the door recognise a good face at all (scores, not vectors) | [`scripts/match-probe`](../../scripts/match-probe) — stops door-visiond for the duration |
| Is the public enrollment relay configured on this Pi | [`scripts/enrollment-qr`](../../scripts/enrollment-qr) preflight |
| Audit a deployment `.env` for duplicate keys, placeholders, inline comments | [`scripts/check-env-secrets`](../../scripts/check-env-secrets) |
| Wait for a service to come up in a unit or a script | [`scripts/wait-http`](../../scripts/wait-http) |
| Operator procedures (Pi bring-up, outages, camera aiming, backups) | [`docs/runbooks/`](../../docs/runbooks/) |

If a consolidated diagnostics tool is ever built, it belongs here; until then this README
exists so the directory's purpose is not misread. Nothing here may run on or be exposed to
public surfaces (`/wallboard`, `/doorpad`, `/visitor`).
