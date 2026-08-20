# ADR-0019: Self-service enrollment from the doorpad

**Status:** Accepted · **Date:** 2026-08-16

Extends [ADR-0009](0009-enrollment-and-biometric-data.md) (enrollment and biometric
data) and [ADR-0016](0016-remote-enrollment-relay.md) (the phone relay). Supersedes
nothing: ADR-0009 requires a confirmed consent version, never an owner.

## Context

Enrollment has always required the owner. The CLI is owner-driven, `POST /enroll` and
`POST /invites` sit behind `AdminAuth`, and `docs/ui/admin.md` describes an admin panel
button (which was never built — the invite API has no caller at all, so in practice
enrollment meant an SSH session and a hand-written `curl`).

The owner asked why an admin is needed for someone to add their own face. Reviewing it,
the requirement does not hold up:

- **Enrollment grants no authority.** "Recognition is personalization, never
  authorization" is an invariant (ADR-0005 §3, ARCHITECTURE.md §9) with a CI check
  behind it (ADR-0009 P-11: nothing under `apps/door-api` may import match results for
  a gating decision). There is no lock on this door. Being enrolled buys a greeting, a
  profile colour, a name in the arrival log, and a name in the owner's Telegram
  notification. Gating that behind the owner's presence is friction protecting little.
- **Self-service improves consent provenance.** The CLI has the *owner* confirm, on the
  enrollee's behalf, that the enrollee agrees — the weakest form of consent in the
  system. A person reading the statement on their own phone and accepting it is
  stronger. E-7 (single consent source) holds either way: both surfaces render
  `docs/policies/consent-statement.md` and both send its version.
- **The doorpad is already unauthenticated by design.** Anyone standing at the door can
  ring the bell, leave a guestbook note, and check in. Physical presence is the
  authorization for that surface. Enrollment is not a different kind of act.

## Decision

**A visitor may enroll themselves, initiated from the doorpad, with no admin
credential.** The doorpad gains an *Enroll me* action that mints a single-use invite
and renders its QR on the touchscreen; the enrollee scans it and completes the existing
ADR-0016 relay flow on their own phone (consent, photos sealed to the door's public key,
collected by outbound poll, embedded, wiped).

Four constraints make that safe:

1. **Presence is the gate.** The minting route is Pi-local and rate-limited, and the QR
   is only rendered on the doorpad. Invites are not mintable from elsewhere on the
   network, and never from the public relay. A person must be at the door to start.
2. **No display-name collisions.** An enrollment whose `display_name` matches an
   already-enrolled person is rejected. Without this, a stranger could enroll as a
   resident's name and be greeted — and announced to the owner — as them. Comparison is
   case-insensitive and whitespace-normalised.
3. **Bounded.** At most `VISIOND_SELF_ENROLL_PER_HOUR` invites (default 6) and
   `VISIOND_SELF_ENROLL_MAX_ENROLLED` total enrolled people (default 50). Beyond either,
   the doorpad says enrollment is closed and refers the visitor to the owner. The point
   is that a passer-by cannot load the encrypted volume with strangers' biometrics.
4. **Never silent.** A self-enrollment is visible without being asked for: door-visiond
   reports `self_enrolled` in `/health` and `/metrics`, `GET /people` names everyone,
   `GET /invites` shows which invites were self-service, and each mint logs
   `self_enroll_invite_minted` with the running counts. Unenroll stays one action and
   still deletes embeddings immediately (ADR-0009 §deletion).

   **A Telegram push is deliberately not part of this ADR.** Notifications are derived
   on the NUC from mirrored contract events, and there is no enrollment event in
   `packages/contracts` — `evaluate_rules` has nothing to fire on. Adding
   `enrollment.person_enrolled` is a schema change to the normative event catalog and
   needs its own ADR (ADR-0003 discipline); bundling it here would smuggle a contract
   change into a UI task. Until then the owner's notice is pull, not push. If that gap
   matters more than the process, the follow-up is small and should be raised as its own
   task rather than widened into this one.

**Authorization is still not granted.** P-11 continues to apply unchanged: a
self-enrolled identity reaches greeting and display fields only. Nothing in this ADR
lets recognition gate anything.

**Owner approval is available but off.** `VISIOND_SELF_ENROLL_REQUIRE_APPROVAL=true`
holds a self-enrolled person as `pending` — stored, consented, not used for greetings or
name attribution — until the owner approves. Default `false`, because the owner asked
for direct enrollment and the invariant above means a pending state protects little.

**Credentials do not move to the door.** The doorpad calls door-api; door-api forwards
to door-visiond over loopback (ARCHITECTURE.md §7) holding the visiond admin token
server-side. `DOOR_VISIOND_ADMIN_TOKEN` never reaches a browser, so the door Pi's kiosk
profile gains no credential — the invite-minting capability is exposed, the admin API is
not.

## Consequences

- The trust boundary moves: a public, unauthenticated surface can now cause a write to
  the encrypted enrollment volume. Bounded by (1)–(4); no other admin capability is
  reachable from the doorpad.
- Anyone with brief physical access to the doorpad can enroll themselves. That is the
  accepted intent, not an oversight. The mitigation is visibility and instant deletion,
  not prevention — consistent with how the bell and guestbook already work.
- The owner may hold biometric data for people they did not personally enrol. The caps
  bound it, the consent record names it, and `GET /people` lists it.
- `docs/ui/doorpad.md` and `docs/ui/admin.md` both need updating; admin.md currently
  describes an enrollment button that does not exist.
- The admin path stays. The CLI is unchanged and remains correct for enrolling someone
  with no phone.
