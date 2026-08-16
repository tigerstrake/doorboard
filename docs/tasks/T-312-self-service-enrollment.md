# T-312: Enroll me — self-service enrollment from the doorpad

**Agent:** claude · **Milestone:** M3 · **Depends on:** T-304, T-306 (ADR-0019 shipped)
**Why this agent:** a public surface gains a write path to the encrypted enrollment
volume. Trust boundary, rate limiting, and the one invariant that must not bend.

## Context

Enrolling a face currently means an SSH session and a hand-written `curl`. The invite API
that ADR-0016 built has **no caller**: `docs/ui/admin.md` and
`docs/runbooks/remote-enrollment-relay.md` both describe an admin-panel button that was
never built, so the runbook reads as step-by-step instructions for a control that does
not exist.

ADR-0019 resolves who may start enrollment: anyone standing at the door. Enrollment
grants no authority (ADR-0009 P-11 enforces it in CI), and having the enrollee read and
accept the consent statement themselves is stronger provenance than the CLI's
owner-confirms-on-their-behalf. Presence at the doorpad is the same authorization the
bell, the guestbook and check-in already rely on.

## Deliverables

- **`POST /self-enroll/invites` on door-visiond** — no admin credential, mints the same
  single-use invite as `POST /invites`, rejects when a cap is hit. Refuses with the same
  503/409 as `/invites` for a locked volume and privacy mode.
- **`POST /visitor/enroll-invite` on door-api** — the doorpad's entry point. Pi-local
  only, rate-limited, forwards over loopback holding `DOOR_VISIOND_ADMIN_TOKEN`
  server-side so no credential reaches the kiosk browser.
- **Caps and guards in door-visiond:** `VISIOND_SELF_ENROLL_PER_HOUR` (default 6),
  `VISIOND_SELF_ENROLL_MAX_ENROLLED` (default 50), and display-name collision rejection
  applied to *every* enrollment path, not just this one.
- ~~**Telegram notification** on each completed self-enrollment~~ — **deferred, not
  built.** Notifications are derived on the NUC from mirrored contract events and there
  is no enrollment event in `packages/contracts`, so this needs a schema change plus its
  own ADR rather than a quiet addition here (see ADR-0019 §4). Shipped instead:
  `self_enrolled` in `/health` and `/metrics`, the self-service label visible in
  `GET /invites`, and a `self_enroll_invite_minted` log line per mint. Pull, not push.
- **Doorpad UI:** an obvious *Enroll me* action; tapping it renders the QR large on the
  7" panel with the expiry counting down, a *new code* action, and an honest closed
  state when a cap is hit. Uses `packages/ui-kit`'s existing `qrcode` dependency.
- ~~**`VISIOND_SELF_ENROLL_REQUIRE_APPROVAL`**~~ — **deferred, not built.** A `pending`
  state needs a schema migration plus exclusion from the matcher, the arrival log and
  Telegram; shipping that half-exercised, off by default, would add deploy risk for a
  switch the owner explicitly does not want. The caps and the name-collision rule are
  what make self-service safe; this was only ever a second belt. Revisit if the owner's
  view changes.
- Docs: `docs/ui/doorpad.md`, `docs/ui/admin.md` (delete the button that never existed),
  `docs/runbooks/remote-enrollment-relay.md` step 1, ARCHITECTURE.md §7 (the new
  api→visiond loopback leg).

## Out of scope

The enrollment CLI, the relay and its sealing (unchanged — this only adds a caller),
greeting visuals, the admin panel's enrollment list, and any change to
`packages/contracts`.

## Acceptance criteria

- A person with only a phone, standing at the door, gets from *Enroll me* to being
  greeted by name without the owner touching anything: verified on the door, not only in
  tests.
- The minting route is unreachable from off-device, and `DOOR_VISIOND_ADMIN_TOKEN` never
  appears in any browser-reachable response or in the kiosk profile.
- A `display_name` colliding with an enrolled person is rejected (case-insensitive,
  whitespace-normalised) on **both** `/enroll` and the self-service path.
- Past `VISIOND_SELF_ENROLL_PER_HOUR`, minting refuses and the doorpad says so plainly;
  past `VISIOND_SELF_ENROLL_MAX_ENROLLED`, likewise.
- A locked enrollment volume or privacy mode refuses self-enrollment with the same
  status codes as the admin path, and the doorpad explains rather than failing silently.
- `test_no_identity_in_authorization_path` (P-11) still passes: a self-enrolled identity
  influences greeting and display fields only.
- The owner can see, without asking anyone, that people added themselves:
  `self_enrolled` in `/health` and `/metrics`, names in `GET /people`, self-service
  invites distinguishable in `GET /invites`. (Telegram push deferred — ADR-0019 §4.)
- With `VISIOND_SELF_ENROLL_REQUIRE_APPROVAL=true`, a pending person is recognised for
  nothing — no greeting, no name in the arrival log, no name in Telegram — until
  approved.
