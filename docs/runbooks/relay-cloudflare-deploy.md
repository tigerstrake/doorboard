# Runbook: deploy the relay to Cloudflare Pages (`door.tigerstrake.com`)

Deploys `apps/public-relay` to Cloudflare Pages on a subdomain of `tigerstrake.com`, off Vercel
and Upstash (ADR-0043). The relay is a blind courier — ciphertext and opaque ids only — so
this changes *where* it runs, not what it holds. The door side changes only its relay URL.

The build sandbox cannot run `wrangler`, so the steps below are the first place the Cloudflare
build and runtime are exercised. Where a step needs verifying, it says so — do not skip those.

## 0. Prerequisites

- `tigerstrake.com`'s DNS is already on Cloudflare (it is — the personal site is there).
- `npm i -g wrangler` (or `pnpm dlx wrangler`), then `wrangler login`.
- Work from `apps/public-relay/`.

## 1. Create the D1 database

```bash
wrangler d1 create doorboard-relay
```
Copy the printed `database_id` into `wrangler.toml` (`database_id = "…"`, replacing the
placeholder). Then create the schema (idempotent):
```bash
wrangler d1 execute doorboard-relay --remote --file=./migrations/0001_init.sql
```
`migrations/0001_init.sql` mirrors `lib/d1/schema.ts`. Confirm five tables plus the two indexes
exist:
```bash
wrangler d1 execute doorboard-relay --remote --command \
  "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
# expect: bundle, door_key, invite, rate_limit, visitor_action, visitor_snapshot
```

## 2. Set the two device-token secrets

These must match what the door presents. They are the SAME values the Vercel deployment used —
copy them from there, or rotate both (and update the door, step 6):
```bash
wrangler pages secret put RELAY_DEVICE_TOKEN          # door-visiond's token (enrollment routes)
wrangler pages secret put RELAY_VISITOR_DEVICE_TOKEN  # door-api's token (visitor routes)
```
No decryption key is ever set here (E-9): the only secrets are these two bearer tokens.

## 3. Build the static site

```bash
pnpm --filter @doorboard/public-relay build   # next build → out/  (output: "export")
```
**Verify the export shape** (the SPA fallback depends on it):
```bash
ls out/e out/v          # expect a placeholder page: e/_.html (or e/_/index.html) and v/_ likewise
cat out/_redirects      # /e/*→/e/_ and /v/*→/v/_ ; copied from public/_redirects
cat out/_headers        # security + cache headers ; copied from public/_headers
```
If the placeholder is `e/_/index.html` rather than `e/_.html`, the `/e/_` rewrite target still
resolves (Pages serves the directory index) — no change needed. If `out/e` is missing entirely,
`generateStaticParams` did not run; check `app/e/[token]/page.tsx`.

## 4. Deploy

```bash
wrangler pages deploy out --project-name doorboard-relay
```
First run creates the project. `functions/api/[[route]].ts` is picked up automatically and
bound to the D1 database and secrets from `wrangler.toml` + step 2.

## 5. Bind the custom domain

In the Cloudflare dashboard → Pages → doorboard-relay → Custom domains → add
`door.tigerstrake.com`. Cloudflare creates the DNS record (the apex/`www` personal site is
untouched). Wait for the certificate to go active.

## 6. Verify end to end (do not skip)

```bash
BASE=https://door.tigerstrake.com
curl -s $BASE/api/health                     # {"...":"ok"...} with pending counts, no enrollee data
curl -s -o /dev/null -w '%{http_code}' $BASE/api/pickup           # 401 (device token required)
curl -s -H "Authorization: Bearer $RELAY_DEVICE_TOKEN" $BASE/api/pickup   # {"items":[]}
```
- Load `$BASE/e/inv_test#s=x&k=y` in a browser → the enrollment shell renders and blocks on the
  (fake) invite. This proves the SPA fallback + client path-reading work.
- With the door online, mint a real invite from the DoorPad and complete an enrollment from a
  phone on cellular. Watch door-visiond logs for a successful pickup + enroll.
- Re-confirm the binding invariants on the live runtime: P-13 (no plaintext stored — the phone
  bundle round-trips as ciphertext), P-15 (a substituted key is refused), P-21/P-22 (only
  `sha256(token)` stored; enrollment vs visitor tokens are rejected on each other's routes).

## 7. Point the door at the new relay

On the door hosts, set the relay URLs to the new origin (no code change — ADR-0043 §4):
- door-visiond: `VISIOND_RELAY_BASE_URL=https://door.tigerstrake.com`
  (and `VISIOND_RELAY_PUBLIC_URL` if it is set separately — the URL the phone loads).
- door-api: `DOOR_API_VISITOR_RELAY_BASE_URL=https://door.tigerstrake.com`
  (and `DOOR_API_VISITOR_RELAY_PUBLIC_URL` if set).
Restart both services. The QR the wallboard renders and the enrollment link the DoorPad shows
now point at Cloudflare. door-api's fail-safe LAN fallback (E-19) is unaffected.

Once verified, the old Vercel deployment is superseded and can be deleted from Vercel; its
Upstash database can be removed too.

## Notes and known-manual bits

- **`nodejs_compat`** is set in `wrangler.toml`; it is required for `lib/device.ts`'s
  `node:crypto` constant-time compares and for the device-token secrets to be readable via
  `process.env`. If a device call unexpectedly 401s, confirm the flag is active on the deployment.
- **Expiry sweep.** Every query filters on expiry, so stale rows are never served — correctness
  does not need a sweep. To reclaim space, run periodically (or wire a scheduled Worker calling
  `D1RelayStore.sweepExpired()`):
  ```bash
  wrangler d1 execute doorboard-relay --remote --command \
    "DELETE FROM bundle WHERE status_expires_ms<=unixepoch()*1000 AND bundle_json IS NULL;
     DELETE FROM invite WHERE expires_ms<=unixepoch()*1000;
     DELETE FROM rate_limit WHERE window_expires_ms<=unixepoch()*1000;
     DELETE FROM visitor_snapshot WHERE expires_ms<=unixepoch()*1000;
     DELETE FROM visitor_action WHERE expires_ms<=unixepoch()*1000;"
  ```
- **Rollback.** The door URL env is the switch: point it back at the Vercel origin (kept live
  until step 7 is verified) and restart the two services. Nothing on the door changed but a URL.
- **The code-level relay bugs** the move already fixed: the invite secret is out of the URL
  (ADR-0043 §2) and the visitor page no longer carries a name (ADR-0044). The deeper
  seal-the-secret hardening remains deferred (needs on-Pi testing) — see the audit backlog.
