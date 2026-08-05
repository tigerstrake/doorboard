# T-307: Make the visitor QR work off the house wifi

**Agent:** claude · **Milestone:** M4 · **Depends on:** T-306 (merged)
**Why this agent:** it moves a public surface across a trust boundary. ADR-0017 authorizes it; this task implements that ADR and nothing more.

## Context

Required reading: [ADR-0017](../adr/0017-public-visitor-relay.md) (binding), [ADR-0016](../adr/0016-remote-enrollment-relay.md) (the pattern being reused), [ADR-0005](../adr/0005-privacy-model.md) §5, ARCHITECTURE.md §2 and §10, [docs/ui/visitor.md](../ui/visitor.md).

The wallboard QR encodes `DOOR_API_VISITOR_PUBLIC_BASE_URL`, default `http://door.local`. A visitor at the door is on cellular, so the page does not load — the feature has been decorative in its primary case. ADR-0017 settles the fix: serve the visitor page from the public relay, with door-api pushing a narrow snapshot of public session state and collecting queued writes on an outbound poll.

Unlike enrollment, this data is public by design; the protection is scope, not encryption. Read ADR-0017's second section before touching anything here — the security argument is different, and applying enrollment's reasoning by analogy will produce the wrong review.

## Deliverables

- `packages/contracts`: visitor snapshot + queued-action shapes, and `VISITOR_SNAPSHOT_FIELDS` exporting ADR-0017 §2's allow-list as data so the list and the code cannot drift.
- `apps/public-relay` (renamed from `enroll-web`, which now misdescribes it):
  - per-service scoped device tokens, each route demanding exactly one (E-17);
  - visitor routes — snapshot push, phone read (token-hash authorised, hash withheld from the response), phone write with rate limits, Pi pickup and ack;
  - `/v/[token]` visitor page: ring status, note, poll vote, deletion request.
- `apps/door-api`:
  - relay client: push on every state transition, poll, apply through the existing `SocialService`, ack with outcomes;
  - action memoisation so a duplicate delivery cannot double-post;
  - QR URL selection with LAN fallback (E-19) and `GET /visitor-relay-status`.
- Docs: ADR-0017, both READMEs, ARCHITECTURE trust/service/failure tables, security-checklist egress entry, runbook.
- Tests: P-20 … P-25 from ADR-0017, by ID.

## Out of scope

- Changing the LAN visitor page's behaviour. door-ui keeps its copy as the offline fallback; the duplication is deliberate (ADR-0017 consequences).
- Any inbound exposure of door-api (no tunnels, no port forwarding).
- Sending anything beyond ADR-0017 §2's snapshot fields or §3's action kinds.
- Media on the visitor path: video messages and photos stay on the Pi→NAS route.
- Moving sanitisation or rate limiting to the relay (E-18).

## Acceptance criteria

- A phone with no access to the house LAN can load the QR, see ring status, leave a note, and vote; the note appears in the guestbook and on the wallboard.
- P-20 … P-25 pass. ADR-0016's P-12 … P-19 and ADR-0009's P-1 … P-11 still pass unchanged.
- With the relay unreachable, the QR encodes the LAN URL, the session flow is unaffected, and `/health` stays `ok`.
- The snapshot contains no identity, media, or diagnostic field, proven by byte-scan rather than inspection.
- `scripts/lint`, `scripts/typecheck`, `scripts/test` green; contracts regeneration clean.
