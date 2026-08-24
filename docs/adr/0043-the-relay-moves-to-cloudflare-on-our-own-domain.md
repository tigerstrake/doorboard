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

**Runtime-compat, decided against the code inventory:**

- **`lib/device.ts` is the only runtime code with Node-only APIs** — `createHash` /
  `timingSafeEqual` (`node:crypto`) and `Buffer`, in the device-token check and the
  invite-secret / visitor-token hash compares. This is security-critical constant-time
  comparison. We enable the Workers **`nodejs_compat`** flag and keep it *unchanged* rather
  than rewrite it on `crypto.subtle` — a rewrite would make `sha256Base64Url`/`digestsMatch`
  async and ripple through four route handlers and the test call-sites, and hand-rolling a
  constant-time compare is exactly the kind of change not to make untested. If a specific API
  proves unsupported on deploy, the WebCrypto rewrite is the documented fallback.
- **What forces D1 over plain KV:** the lease queues are Redis sorted-sets (`pending`,
  `visitor:pending`, scored by time), the rate limiter is `INCR`+`EXPIRE`, and the
  invite-`consumed` flag, the terminal-status guard, and the visitor `outcomes[]` are
  read-modify-write. KV has neither sorted sets nor atomic increment. (`@upstash/redis` is
  fetch-based and *would* run on Workers, but the owner's decision is off-Upstash, so it is
  dropped.) Note today's code performs these read-modify-writes **non-atomically**, relying on
  Upstash per-key serialization plus the single-threaded Pi poller; D1 transactions make them
  actually atomic, an improvement, not just a port. One incidental bug to fix in passing:
  `putDoorKey` writes `doorkey:active` with **no TTL** despite the "TTL on every key" claim.

### 2. The invite secret leaves the URL path (amends ADR-0016 §4 / E-11)

Today the enrollment link is `…/e/<invite_id>.<secret>`, so the secret sits in the **request
line** of the page load — visible in the relay's access logs, the browser history, and any
`Referer` header. ADR-0016 §4 verifies the secret at the relay for spam control, so a
compromised relay already sees it at *submit* time; but the path placement leaks it far more
widely and passively, which is what falsifies the "reads it from its own request line" case.

The inventory shows the secret is worse-placed than "the page URL": the client also puts the
whole `<invite_id>.<secret>` token in the **API request path** on both the invite-state `GET
/api/enroll/<token>` and the `POST …/submit` (`EnrollFlow.tsx:79,293`), so the secret reaches
the origin in the request line of two API calls, not just the page load. Moving it out of the
page URL alone would not fix that.

The fix therefore has two parts: (a) the enroll link becomes `…/e/<invite_id>#s=<secret>&k=<fp>`
— the secret in the fragment, exactly as the key fingerprint already is (§3), never sent to a
server on the page load; the client reads it from `location.hash`. (b) The API path carries
only `<invite_id>`; the client presents the secret in a request **header**
(`X-Doorboard-Invite-Secret`), which the two handlers read instead of splitting it off the
path. The relay still hashes it for the §4.1 spam check exactly as before — the secret simply
never appears in a URL/request line (logs, history, `Referer`) again. This is relay-side plus a
one-line change to door-visiond's URL builder (`service.py:799`) and its test; it is
platform-independent, so it can land ahead of the platform move. P-32 pins it.

**Follow-up, deferred (not in this change):** to restore E-11's *full* "a compromised relay
cannot cause an enrollment" property against the submit-time exposure, the relay's spam check
should take `sha256(secret)` from the client (never the raw secret), with the raw secret
sealed inside the manifest for the Pi's authoritative check (§4.2). That is a change to the
enrollment *crypto protocol* spanning door-visiond, the Pi's pickup verification, and
`packages/contracts` — and it must be verified against real hardware, which is offline during
this change. It is recorded in the backlog and gets its own ADR when the door can be tested.

### 3. The visitor-page attribution exposure — escalated to the owner, not changed here

Per ADR-0017 §2 (as amended by ADR-0018 §2) the visitor snapshot carries `attributed_to` —
the recognised person's own name — so the page can disclose "posting as Tiger" *before* a
write (E-23). The inventory settles what this is: **`app/api/visitor/[token]/route.ts:42`
returns `attributed_to` to anyone who presents a token whose `sha256` matches the snapshot**,
and the relay has **no mechanism binding the token-holder to the recognised person** — door-api
computes the name from face recognition at the door, while the token is simply whatever phone
scanned the wallboard QR. So a different person who scans that session's QR receives the
resident's real name. The existing tests (`visitorFlow.test.tsx:183`, `visitor.test.ts:117`)
assert the name *is* present, because ADR-0017/0018 deliberately put it there.

This is therefore a **design decision, not a code defect**: "fixing" it means either removing
the name from the relay path (weakening E-23's explicit "say the name" disclosure) or narrowing
when door-api populates it — both are amendments to ADR-0017 §2 / ADR-0018 E-23, which are
binding. Per the coordinator rule, an architecture flaw is resolved by an ADR/owner decision,
not by an implementer improvising. **This ADR does not change `route.ts:42`.** The options are
put to the owner; the chosen behaviour lands in a follow-up amendment. The platform move ships
without touching attribution, so the two concerns stay independent.

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
| P-32 | `test_invite_secret_never_in_a_url` — the enroll link the Pi builds carries the secret only in the `#s=` fragment (never the path/query); the client's API calls carry `<invite_id>` in the path and the secret in the `X-Doorboard-Invite-Secret` header, so no request line — page load or API — contains the secret. The relay still rejects a hash-mismatched secret (non-enumerable), unchanged. | public-relay / visiond |
| P-33 | `test_d1_store_preserves_lease_and_terminal_semantics` — single-use invite consumed once under concurrent pickups; a lapsed lease re-collects; a terminal status never regresses on a duplicate ack — the D1 store passes the same assertions the Redis store did. | public-relay |

## Consequences

- The relay's production origin changes from `*.vercel.app` to `door.tigerstrake.com`. The QR
  content changes accordingly; door-api's fail-safe LAN fallback (E-19) is unaffected.
- `@upstash/redis` leaves the relay's dependencies; a D1 binding and `wrangler` config enter,
  with `nodejs_compat` enabled so the device/hash crypto in `lib/device.ts` is unchanged. The
  Next server runtime is no longer used in production (pages static, API as Functions).
- Attribution (`attributed_to`) is deliberately **untouched** by this change; it is escalated
  to the owner (§3) and any change to it is a separate ADR-0017/0018 amendment.
- The residual "host can serve malicious JS" risk (ADR-0016 §9) now rests on the owner's
  Cloudflare account rather than Vercel — reduced, not removed. The consent statement's
  "encrypted photos through a relay that cannot read them" wording stays true and needs no
  bump; only the deployment docs and security checklist change hosts.
- ADR-0016 §5's Vercel-KV table is superseded by the D1 schema in the deploy runbook; the
  retention windows it specifies are preserved exactly.
- This ADR does **not** change the door interaction path, recognition, the contract firewall,
  or any privacy invariant. It relocates a courier and moves one secret out of a URL path.
