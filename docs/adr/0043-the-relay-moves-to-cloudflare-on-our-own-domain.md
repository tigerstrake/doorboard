# ADR-0043: the public relay moves to Cloudflare, on our own domain

**Status:** Accepted · **Date:** 2026-08-25 · **Supersedes:** ADR-0016 §5 (relay data model / Vercel KV) and the "Vercel" platform naming in ADR-0016 §2/§9 and ADR-0017 · **Amends:** ADR-0016 §4 / E-11 (invite-secret placement) · **Upholds:** every enforcement mechanism E-8…E-20 and every test P-12…P-25 (they are platform-independent and remain binding)

## Context

The relay (`apps/public-relay`, ADR-0016/0017) runs as a Next.js app on **Vercel** with
**Upstash Redis** for state, at `doorboard-relay.vercel.app`. The owner wants it off Vercel
and onto a domain they already own and control — `tigerstrake.com`, whose DNS and static
personal site already live on **Cloudflare**. Two motivations, both legitimate:

1. **Own the origin.** ADR-0016 §9 accepts one residual risk explicitly: "a fully
   compromised Vercel deployment can serve malicious JavaScript" that exfiltrates plaintext
   before the browser seals it. Hosting the relay on the owner's own Cloudflare account, from
   source in their own git, under their own domain, does not *eliminate* that class (any
   browser-delivered crypto carries it) but it moves the trusted party from a third-party
   PaaS to the owner. That is a real reduction in who must be trusted.
2. **Consolidate.** The domain, DNS, and the personal site are already on Cloudflare. One
   platform, one account, one bill, one deploy story.

The personal-site repo (`~/dev/ClaudeCode personal website`) was evaluated as a host and
**cannot** hold the relay: it is Next.js with `output: "export"` — a fully static site served
by Cloudflare Pages from a checked-in `out/` directory with no build step and no server
runtime. A static export has no place to run the relay's ~16 server-side API routes and no
datastore. The relay therefore moves *beside* the personal site — a separate Cloudflare
project on a subdomain — not *into* it.

The door side does not care where the relay lives. Both services reach it purely by env URL —
door-visiond via `VISIOND_RELAY_BASE_URL` (+ `relay_public_url` for the link shown to the
phone), door-api via `DOOR_API_VISITOR_RELAY_BASE_URL` (+ `visitor_relay_public_url`). Moving
the relay is a URL change on the door, not a code change.

## Decision

### 1. Platform: Cloudflare Pages + Functions, one D1 database, on a subdomain

The relay is served from a **Cloudflare Pages** project bound to **`door.tigerstrake.com`**
(a Cloudflare DNS record; the apex/`www` personal site is untouched):

- **The two pages** (`/e/<token>` enrollment, `/v/<token>` visitor) become **static** and
  client-rendered. They already are client flows that call `/api/*`; `output: "export"` plus
  moving the last of their data-fetching to the client makes them static-serveable with no
  server runtime. This sidesteps running the (very new) Next 16 server runtime on Cloudflare.
- **The ~16 API routes** become **Cloudflare Pages Functions** (`functions/api/**`), plain
  `onRequest*` handlers over the Web `Request`/`Response` and `env` bindings — no Next server
  APIs. Each handler's logic is a pure `(request, env) => Response` function so the existing
  vitest suite exercises it unchanged (see §5).
- **State moves from Upstash Redis to one Cloudflare D1 database** (SQLite, strongly
  consistent, transactional). D1 — not KV — because the relay's correctness rests on
  operations KV's eventual consistency cannot guarantee:
  - **single-use invites** (E-11): consuming an invite must be atomic;
  - **the pickup lease** (a bundle is *leased*, not deleted, on collection, so a Pi that dies
    mid-pickup re-collects after the lease lapses): a compare-and-set;
  - **terminal status never regresses** (`enrolled`/`expired` are final, so a late duplicate
    ack cannot un-enroll someone): a conditional update;
  - **rate-limit counters**: read-modify-write.
  D1 gives all four a real transaction. TTLs from ADR-0016 §5 become `expires_at` columns
  swept lazily on read (and by a scheduled Worker), preserving the exact retention windows
  (bundle 15 min, status 1 h, invite grace 1 h, pickup lease 60 s). At a single dorm door the
  volume is trivially within D1 limits; Durable Objects would also work but are more machinery
  than this needs. The storage module keeps a single narrow interface (as `store.ts` already
  does with `RedisLike`); the D1 implementation is one file behind it.

Nothing in the *shape* of the relay changes: same routes, same request/response contracts
(`packages/contracts`, E-13), same blind-courier semantics, same scoped device tokens
(E-17). What changes is the runtime and the datastore.

### 2. The invite secret leaves the URL path (amends ADR-0016 §4 / E-11)

Today the enrollment link is `…/e/<invite_id>.<secret>`, so the secret sits in the **request
line** of the page load — visible in the relay's access logs, the browser history, and any
`Referer` header. ADR-0016 §4 verifies the secret at the relay for spam control, so a
compromised relay already sees it at *submit* time; but the path placement leaks it far more
widely and passively, which is what falsifies the "reads it from its own request line" case.

The secret moves into the **URL fragment**, exactly as the key fingerprint already does
(§3): `…/e/<invite_id>#s=<secret>&k=<fp>`. Fragments are never sent to any server. The client
reads the secret from the fragment and presents it only in the submit body, as before. This
closes the passive path leak (logs, history, `Referer`) — the decided, in-scope fix, and the
one the backlog names.

**Follow-up, deferred (not in this change):** to restore E-11's *full* "a compromised relay
cannot cause an enrollment" property against the submit-time exposure, the relay's spam check
should take `sha256(secret)` from the client (never the raw secret), with the raw secret
sealed inside the manifest for the Pi's authoritative check (§4.2). That is a change to the
enrollment *crypto protocol* spanning door-visiond, the Pi's pickup verification, and
`packages/contracts` — and it must be verified against real hardware, which is offline during
this change. It is recorded in the backlog and gets its own ADR when the door can be tested.

### 3. The visitor page stops handing a name to any token holder

Per ADR-0017 §2 (as amended by ADR-0018 §2) the visitor snapshot carries `attributed_to` —
the recognised person's own name — so the page can disclose "posting as Tiger" *before* a
write (E-23). The exact current exposure and the minimal correct fix are pinned to the code
inventory of `app/v/[token]` and `/api/visitor/*`; the decision here is the **invariant**:
the relay-served visitor page discloses attribution to the recognised person without turning
the token-addressable snapshot into a way for *any* holder of the QR to read who is currently
at the door. Whatever the fix's mechanics, it must not widen the ADR-0017 §2 allow-list and
must keep `attributed_to` null unless consent covers attribution (ADR-0018 §2). If the correct
fix requires revisiting the attribution *model* (not just its transport), that is escalated to
the owner rather than improvised here — the allow-list is binding.

### 4. Door-side and deployment changes

- **Door config:** point `VISIOND_RELAY_BASE_URL` / `relay_public_url` and
  `DOOR_API_VISITOR_RELAY_BASE_URL` / `visitor_relay_public_url` at
  `https://door.tigerstrake.com`. No door code changes for the platform move; the §2 fragment
  change is a one-line edit to door-visiond's enroll-URL builder (and its unit test).
- **Secrets** move from Vercel/Upstash env to Cloudflare: the two scoped device tokens
  (`RELAY_DEVICE_TOKEN`, `RELAY_VISITOR_DEVICE_TOKEN`, E-17) become Pages project secrets; the
  Upstash URL/token bindings are replaced by a D1 binding. **No decryption key exists on the
  relay on either platform** (E-9) — that invariant is unchanged and re-verified.
- **Deploy** is documented in a new `docs/runbooks/relay-cloudflare-deploy.md`: `wrangler`
  project setup, the D1 schema migration, binding the custom domain, and rotating the device
  tokens. Cloudflare's local runtime (`wrangler`/miniflare) is **not** available in the build
  sandbox, so pre-deploy verification is the vitest suite + typecheck (§5); first real-runtime
  verification happens on deploy, against the P-13/P-15/P-21/P-22 checks.

### 5. Verification

Every ADR-0016/0017 test (P-12…P-25) is platform-independent and stays binding. The relay's
existing 96-test vitest suite (with its in-memory storage double) is the regression net and
must stay green through the port; the D1 implementation gets its own adapter tests mirroring
the `fakeRedis` double. Two properties get an explicit new test on the new platform:

| ID | Test | Where |
|---|---|---|
| P-32 | `test_invite_secret_never_in_a_server_request` — the enroll URL the Pi builds carries the secret only in the fragment; a simulated page load exposes no secret in path, query, or any header the Function receives. | public-relay / visiond |
| P-33 | `test_d1_store_preserves_lease_and_terminal_semantics` — single-use invite consumed once under concurrent pickups; a lapsed lease re-collects; a terminal status never regresses on a duplicate ack — the D1 store passes the same assertions the Redis store did. | public-relay |

## Consequences

- The relay's production origin changes from `*.vercel.app` to `door.tigerstrake.com`. The QR
  content changes accordingly; door-api's fail-safe LAN fallback (E-19) is unaffected.
- `@upstash/redis` leaves the relay's dependencies; a D1 binding and `wrangler` config enter.
  The Next server runtime is no longer used in production (pages static, API as Functions).
- The residual "host can serve malicious JS" risk (ADR-0016 §9) now rests on the owner's
  Cloudflare account rather than Vercel — reduced, not removed. The consent statement's
  "encrypted photos through a relay that cannot read them" wording stays true and needs no
  bump; only the deployment docs and security checklist change hosts.
- ADR-0016 §5's Vercel-KV table is superseded by the D1 schema in the deploy runbook; the
  retention windows it specifies are preserved exactly.
- This ADR does **not** change the door interaction path, recognition, the contract firewall,
  or any privacy invariant. It relocates a courier and moves one secret out of a URL path.
