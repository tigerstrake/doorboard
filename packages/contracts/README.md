# packages/contracts — typed event and API contracts

**The single source of truth for every message in the system.** Task: T-002 (Codex, Claude-reviewed). Changes require an ADR (ADR-0001/0003).

## Contents

- Pydantic v2 models for the envelope and every event in [docs/protocols/events.md](../../docs/protocols/events.md) — the doc is normative; this package implements it.
- The visitor session `State` enum and legal-transition table (door-api owns runtime behavior; the *shape* lives here).
- Shared value types: presence labels, storage status, health payloads, error envelope.
- JSON Schema export (`contracts export-schemas`) → `schemas/` (generated, committed).
- TypeScript type generation uses the package's deterministic Python generator (`contracts generate-ts`) over the Pydantic model graph. This keeps the generated TypeScript aligned with the same models that export JSON Schema, avoids a second schema compiler dependency, and makes CI drift checks simple.
- Generated TypeScript is committed in `types/index.ts` and exported as `@doorboard/contracts`; `door-ui` and `ui-kit` consume those types directly.

## Rules

- No service defines its own message shapes. Hand-written duplicate types (Python or TS) are a review-blocking defect.
- Backward compatibility: additive changes preferred; renames/removals need a version bump and migration notes in the ADR.
- Validation is strict (`extra="forbid"`); events that fail validation are logged (metadata only) and counted, never silently coerced.
- Zero I/O in this package — pure models/serialization, importable everywhere including the simulator and tests.

## Commands

- `contracts export-schemas` writes deterministic JSON Schema files to `schemas/`.
- `contracts generate-ts` writes deterministic TypeScript types to `types/index.ts`.
- `contracts export-fixtures` writes one fixture JSON file per event type to `../../tools/seed-data/events/`.
- `scripts/check-generated-contracts` is the CI drift check for generated schemas and TypeScript.
