# T-504: Presence / Weasley-clock engine

**Agent:** gemini · **Milestone:** M5 · **Depends on:** T-501
**Why this agent:** simple, fully specified precedence logic — but privacy-adjacent, so review will check the data-minimization rules hard.

## Context

Handoff §2.B/§13-Focus; contract: `status.presence_changed` in [events.md](../protocols/events.md). The precedence order is fixed and non-negotiable: **manual > focus_shortcut > geofence_label > calendar > default**.

## Deliverables

- Presence engine in control-plane-api: per-subject (owner, roommate) source registry, precedence resolution, `until` expiry handling (e.g. "busy until 15:00" falls through to next source after), change-only event emission.
- Source inputs: manual override endpoint (admin UI + one-tap), HA webhook ingestion for Focus shortcuts and voluntary geofence labels (label strings only — reject payloads containing coordinates: validation error, logged), calendar inference stub behind a `CalendarProvider` interface with mock (real calendar wiring is a later brief).
- Broad labels only, from the fixed eight-label enum; anything else is a validation error.
- Distribution: presence state into the config/data bundle the Pi caches (wallboard tile works during NUC outage with staleness marker).
- Admin UI: current state per subject with source attribution ("busy — manual, until 15:00"), override control, per-source enable/disable.
- History: durable label-change history (label + source + timestamp only), retention-capped.

## Out of scope

Real calendar integration, phone shortcut authoring (documented for the owner, not automated), roommate consent flows beyond a config flag per subject, wallboard tile visuals (exists).

## Acceptance criteria

- Table-driven tests over all source-combination × expiry cases; coordinate-bearing payload rejected with test coverage.
- Manual override set → wins instantly over everything; cleared → next source takes over correctly.
- NUC-outage drill: wallboard shows last-known label with staleness hint (simulator scenario).
