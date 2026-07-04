# T-403: Guestbook, polls, check-ins — CRUD + safety rails

**Agent:** gemini · **Milestone:** M4 · **Depends on:** T-401
**Why this agent:** classic CRUD with an exact safety spec to follow. The safety requirements are the task — skipping any of them fails review.

## Context

Handoff §13 (moderation requirements), [docs/ui/doorpad.md](../ui/doorpad.md), [docs/ui/visitor.md](../ui/visitor.md), api-conventions.md (rate limits, sanitization). Data lives Pi-local (SQLite) and mirrors to the control plane later via door-sync — build against door-api's local storage now.

## Deliverables

- door-api endpoints + SQLite models: guestbook (create/list-approved/delete), polls (current poll, vote — one per session token, results), check-ins (create voluntary, list), each emitting its `social.*` event.
- **Safety rails on every public write:** length caps, HTML-escape-on-render (store raw, escape at boundary; no HTML rendering of UGC anywhere), per-IP+session rate limits, timestamps, soft-delete for moderation, `social.deletion_requested` honored end-to-end.
- DoorPad screens: guestbook (canned phrases + short free-text), poll voting, check-in ("I was here", optional enrolled-person attribution *only* when an identity is cached-and-consented); visitor phone page: unanswered-note + poll vote.
- Admin moderation panel: pending/approved lists, delete, poll creation/closing.
- Most-frequent-visitor stat from voluntary check-ins only (playful display copy).
- Tests: injection attempts (`<script>`, markdown, emoji floods), rate-limit behavior, vote-once enforcement, deletion propagation.

## Out of scope

Control-plane mirroring (T-502/T-501), scoreboard/mood (T-605), wallboard tile rendering beyond feeding existing tiles (T-404 owns visitor-mode visuals), any change to contracts or auth (escalate).

## Acceptance criteria

- Injection test corpus renders inert everywhere (doorpad, wallboard highlight tile, admin).
- Rate limits provably enforced (tests) and tuned per brief defaults (e.g. 5 writes/min/IP — configurable).
- A deletion request removes content from every public surface within one refresh cycle and is audit-logged.
