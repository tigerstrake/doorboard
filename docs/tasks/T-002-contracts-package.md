# T-002: Contracts package — events, schemas, generated TS types

**Agent:** codex · **Milestone:** M0 · **Depends on:** T-000
**Why this agent:** the single most leverage-heavy code in the repo; every service depends on it. Claude-tier review will be strict.

## Context

[docs/protocols/events.md](../protocols/events.md) is the normative catalog. This task implements it in `packages/contracts` per that doc, [packages/contracts/README.md](../../packages/contracts/README.md), and ADR-0003. Typed contracts precede all business logic (handoff §19.3).

## Deliverables

- Pydantic v2 models: envelope + every event type in the catalog, `extra="forbid"`, UUIDv7 event IDs, discriminated union on `type` for parsing arbitrary events.
- Session `State` enum + legal-transition table (data, not logic) exactly matching events.md §session.
- Shared value types: presence labels, error envelope, health payload.
- `contracts export-schemas` CLI (entry point) → JSON Schema files in `packages/contracts/schemas/` (committed).
- TS generation pipeline (choose tool, document choice in package README) → generated types consumed by `door-ui`/`ui-kit`; committed, with a CI check that regeneration produces no diff.
- Round-trip tests: every event type constructs → serializes → parses → equals; invalid payloads (wrong field, extra field, naive datetime) rejected; fixture JSON files in `tools/seed-data/` for each type.

## Out of scope

Event *transport* (event-client package, later tasks), any service logic, changing the catalog — if events.md is ambiguous or wrong, open an `escalation` issue; the doc gets fixed first.

## Acceptance criteria

- 100% of catalog event types implemented and round-trip tested; naive-datetime and extra-field rejection proven by tests.
- Schema export + TS generation both deterministic (running twice = no diff); CI drift check in place.
- Zero I/O imports in the package (pure models); imports cleanly in Python 3.12.
- Claude-tier review sign-off recorded on the PR.
