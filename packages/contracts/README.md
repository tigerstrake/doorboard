# packages/contracts — typed event and API contracts

**The single source of truth for every message in the system.** Task: T-002 (Codex, Claude-reviewed). Changes require an ADR (ADR-0001/0003).

## Contents

- Pydantic v2 models for the envelope and every event in [docs/protocols/events.md](../../docs/protocols/events.md) — the doc is normative; this package implements it.
- The visitor session `State` enum and legal-transition table (door-api owns runtime behavior; the *shape* lives here).
- Shared value types: presence labels, storage status, health payloads, error envelope.
- JSON Schema export (`contracts export-schemas`) → `schemas/` (generated, committed).
- TypeScript type generation from schemas → consumed by door-ui/ui-kit. Generated TS is committed with a CI check that regeneration is clean (no drift).

## Rules

- No service defines its own message shapes. Hand-written duplicate types (Python or TS) are a review-blocking defect.
- Backward compatibility: additive changes preferred; renames/removals need a version bump and migration notes in the ADR.
- Validation is strict (`extra="forbid"`); events that fail validation are logged (metadata only) and counted, never silently coerced.
- Zero I/O in this package — pure models/serialization, importable everywhere including the simulator and tests.
