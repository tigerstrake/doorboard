# T-604: Printer status adapter

**Agent:** gemini · **Milestone:** M6 · **Depends on:** T-501

## Context

Spec: [integrations/printer/README.md](../../integrations/printer/README.md). Read-only by construction — the adapter must not even contain control endpoints.

## Deliverables

- `PrinterProvider` interface + `mock`; concrete backend implementation for the owner's printer (confirm OctoPrint vs Moonraker vs vendor API in the issue before starting — one comment, then build).
- `ambient.printer_status` emission (state, job name, progress, ETA) via wallboard-worker; feature flag.
- Optional camera preview: if the printer exposes a stream, publish its URL/proxy path via door-media stream metadata (label only; no printer ports on public screens) — else skip, documented.
- Fixture tests for all five states including `offline`.

## Out of scope

Any control capability (pause/cancel/temps) — structurally absent, not just hidden; printer network setup; tile visuals.

## Acceptance criteria

- All states render on the tile from mock; real backend verified against the owner's printer.
- Code review grep: no POST/control routes exist in the adapter.
- Printer offline → graceful `offline` state, no worker errors.
