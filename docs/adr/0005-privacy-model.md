# ADR-0005: Privacy model — opt-in recognition, personalization-only, minimal retention

**Status:** Accepted · **Date:** 2026-07-04

## Context

The door assembly faces a shared hallway. Face recognition of passersby without consent is both an ethical non-starter and a stated non-goal. The handoff defines strict privacy requirements (§2.3, §10).

## Decision

1. **Enrollment is opt-in and manual.** Only explicitly consenting people, enrolled through the authenticated admin flow, are ever matched. Enrollment records carry consent state and a revocation path; unenrollment deletes embeddings immediately.
2. **Unknown faces produce nothing durable.** No names, no persisted embeddings, no log entries containing biometric data. Unknown visitors get the generic greeting.
3. **Recognition is personalization, never authorization.** No unlock, no access decision, no security function may consume identity events.
4. **Privacy mode** (toggleable from admin UI and physical config) disables recognition entirely while preserving button/UI/recording-by-request behavior.
5. **Public surfaces show broad data only.** Presence uses the eight broad labels; no GPS, no calendar contents, no full visitor logs, no diagnostics on public routes. Manual status override always outranks inference.
6. **All voluntary data is deletable.** Video messages, guestbook entries, poll votes, check-ins, photos, and enrollments each have a deletion flow; visitor-facing deletion requests are honored.
7. **Visible notice.** The assembly carries visible camera/video-message notice text.
8. **Opaque IDs.** `person_id` is an opaque internal identifier; a display name is never a key.

## Consequences

- PRs touching enrollment, embeddings, retention, identity events, or public display content require Claude-tier review (enforced via CONTRIBUTING.md and PR template checklist).
- Tests must cover: unknown-embedding non-persistence, privacy-mode behavior, deletion flows, and public-route data exposure.
- Log schemas are reviewed for biometric leakage; raw frames/embeddings never appear in logs or error reports.
