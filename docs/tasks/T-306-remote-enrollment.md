# T-306: Remote enrollment — invite QR, blind-courier relay, phone capture

**Agent:** claude · **Milestone:** M3 · **Depends on:** T-302 (merged), T-304 (merged)
**Why this agent:** privacy-critical and it crosses a new trust boundary. ADR-0016 authorizes it; this task implements exactly that ADR and nothing more.

## Context

Required reading: [ADR-0016](../adr/0016-remote-enrollment-relay.md) (binding design), [ADR-0009](../adr/0009-enrollment-and-biometric-data.md) §1/§5/§6, [ADR-0005](../adr/0005-privacy-model.md), [docs/policies/consent-statement.md](../policies/consent-statement.md) (now v2), ARCHITECTURE.md §2 trust model.

The owner wants to finish a face enrollment from a phone that is not on the house LAN, from a QR code that resolves anywhere. ADR-0016 settles how: photos are sealed in the browser to a door-held P-256 key, a Vercel app stores only ciphertext under a 15-minute TTL, and the Pi collects by outbound poll, decrypts, embeds, and wipes. The at-door flow stays the default and is unchanged in behavior.

## Deliverables

- `packages/contracts`: relay wire shapes as non-event API models (Python + generated TS + JSON schemas), per E-13. Ciphertext is base64url `str`; no bytes or float-sequence fields anywhere.
- `apps/door-visiond`:
  - relay keypair management (private key on the encrypted enrollment volume, `door_key_id`, fingerprint, rotation with retired-key retention);
  - sealed-bundle open (P-256 ECDH → HKDF-SHA256 → AES-256-GCM, AAD binding per ADR-0016 §2);
  - invite store (SQLite, `sha256(secret)` only) with mint/list/revoke and single-use consumption in the enrolling transaction;
  - outbound relay client task: register invites, publish door key + consent text, poll pickup, decrypt, call existing `enroll()`, ack. Bounded backoff, hard timeouts, never in the door path;
  - admin endpoints for invite mint/list/revoke and relay status; `/health` + `/metrics` additions.
- `apps/enroll-web`: Next.js app on Vercel. `/e/[token]` flow — verify invite, render consent v2 verbatim from the Pi-published copy, capture via `getUserMedia`, pin the key fingerprint from the URL fragment, seal, submit, poll status. API routes for door-key/invite/submit/status/pickup/ack backed by Vercel KV with TTLs. Rate-limited. No admin surface (E-14).
- `apps/door-ui`: admin invite panel that mints an invite and displays the QR on the doorboard, shows pending/completed remote enrollments, and revokes. At-door capture keeps using the real Pi camera via door-media `/snapshot`.
- Docs: deployment + env-var reference for the relay, security-checklist additions, runbook for key rotation.
- Tests: P-12 … P-19 from ADR-0016 §8, by ID.

## Out of scope

- Changing the at-door enrollment flow's behavior or the existing `/enroll` contract.
- Any inbound exposure of the Pi (no tunnels, no port forwarding, no reverse proxy to door-visiond).
- Storing anything about enrollees on the relay beyond ADR-0016 §5's table.
- Recognition/matching changes; the pipeline is untouched.
- Any use of identity in an authorization decision (ADR-0005 §3, permanently out of scope).

## Acceptance criteria

- Full remote cycle works against a local relay in CI (mock mode) and against the deployed relay on hardware: mint invite → scan → capture → seal → collect → enrolled → recognized at the door.
- P-12 … P-19 all pass. ADR-0009's P-1 … P-11 still pass unchanged.
- Relay down, relay hanging, privacy mode on, and enrollment volume locked each degrade exactly as ADR-0016 §6 specifies, with the button path unaffected.
- No plaintext image, display name, or embedding is reachable from any relay-side store or log (P-13, P-19).
- `scripts/lint`, `scripts/typecheck`, `scripts/test` green; contracts regeneration check clean.
