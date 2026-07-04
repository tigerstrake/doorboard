# tools/diagnostics

Operator diagnostics (grows through M1–M7): health sweep across all services, ESP32 link check (heartbeat age, rx errors, round-trip), camera/stream probe, Hailo status, SSD health/free space, sync queue inspection, latency snapshot vs budgets, config bundle version check. Output is human-readable + JSON; surfaced in `/diagnostics` admin route. Never runs on or exposes to public surfaces.
