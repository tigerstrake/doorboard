# Runbook: deploy the relay to Cloudflare Pages (`door.tigerstrake.com`)

**Verified-on:** not yet recorded — fill in (date, wrangler version) after the next deploy walk.

Deploys `apps/public-relay` to Cloudflare Pages on a subdomain of `tigerstrake.com`, off Vercel
and Upstash (ADR-0043). The relay is a blind courier — ciphertext and opaque ids only — so
this changes *where* it runs, not what it holds. The door side changes only its relay URL.

The build sandbox cannot run `wrangler`, so the steps below are the first place the Cloudflare
build and runtime are exercised. Where a step needs verifying, it says so — do not skip those.

## Fast path (one script)

`deploy/relay-cloudflare-deploy.sh` automates everything that doesn't require you personally —
creating the D1 database, wiring its id into `wrangler.toml`, applying the schema, building the
static site, and deploying. You do only the three things a script can't: the interactive login,
pasting the two token values into wrangler's own prompt, and the domain click.

```bash
npx wrangler login                      # once (browser OAuth; no global install, no sudo)
./deploy/relay-cloudflare-deploy.sh     # does steps 1–6 below; prompts you for the two secrets
# then: add the custom domain (step 5) and, when the door is back online, point its env (step 7)
```

(The script runs wrangler via `npx`, so `npm i -g wrangler` — which needs sudo on a default
npm prefix — is not required. If you prefer a global install, `sudo npm i -g wrangler` also works.)

It is idempotent — safe to re-run. The rest of this file is the manual reference the script
follows, and the verification + rollback detail.

## 0. Prerequisites

- `tigerstrake.com`'s DNS is already on Cloudflare (it is — the personal site is there).
- `npx wrangler login` (no install needed). A global `sudo npm i -g wrangler` also works but
  isn't required — the script and the commands below run wrangler via `npx`.
- Work from `apps/public-relay/`.

## 1. Create the D1 database

```bash
wrangler d1 create doorboard-relay
```
Copy the printed `database_id` into `wrangler.toml` (`database_id = "…"`, replacing the
placeholder). Then apply **every** migration, in order (each is idempotent; skipping one is
not — a route that needs a missing table answers 500 by design until it lands):
```bash
for f in $(ls migrations/*.sql | sort); do
  wrangler d1 execute doorboard-relay --remote --file="./$f"
done
```
`migrations/0001_init.sql` mirrors `lib/d1/schema.ts`; later files add to it. Confirm the
tables exist (five from `0001`, plus whatever later migrations add):
```bash
wrangler d1 execute doorboard-relay --remote --command \
  "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
# expect: bundle, door_key, invite, rate_limit, visitor_action, visitor_snapshot
```

## 2. Set the two device-token secrets

These must match what the door presents. Generate **fresh** values (`openssl rand -hex 32`
each) and set the same two on the door (step 7) — do not carry the Vercel-era tokens over;
two relays honouring one token pair is the situation *Decommissioning* below removes:
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
curl -s $BASE/api/health                     # {"service":"public-relay","status":"ok"} — public body only;
                                             # pending counts / door_checked_in need the door-visiond device token
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

Once verified, decommission the old deployment — see below. Until that is done two relays
accept the door's tokens.

## 8. Decommissioning the Vercel deployment (owner actions)

The Vercel/Upstash relay is superseded, not gone. Until these are done, the door's device
tokens open two relays and a laptop file holds credentials to a datastore that once held
enrollment ciphertext and visitor snapshots. In this order:

1. **Rotate both device tokens.** New `RELAY_DEVICE_TOKEN` and `RELAY_VISITOR_DEVICE_TOKEN`
   on Cloudflare (`wrangler pages secret put …`), the same new values on the door
   (`VISIOND_RELAY_DEVICE_TOKEN`, `DOOR_API_VISITOR_RELAY_DEVICE_TOKEN`), restart
   door-visiond and door-api, confirm `door_checked_in` on the Cloudflare relay (`/api/health`
   presented with the new door-visiond device token). The old
   pair — the one Vercel still honours — is now dead everywhere.
2. **Delete the Vercel project** (dashboard → project → Settings → Delete, or
   `vercel remove <project>`). Its `*.vercel.app` origin stops serving.
3. **Delete the Upstash database** (Upstash console → the relay's Redis DB → Delete). Its
   contents were TTL-bounded ciphertext, hashes and snapshots, but the credentials to it
   should stop working regardless.
4. **Delete `apps/public-relay/.env.local`** on the laptop. It is git-ignored and never
   committed, but it still carries the Upstash URL/tokens, the old device tokens and a
   Vercel OIDC token in plaintext: `rm apps/public-relay/.env.local`. Nothing in the
   Cloudflare deploy reads it.
5. Update `docs/runbooks/security-checklist.md`'s relay rows if anything above changed
   what the relay's environment holds.

After step 2 the rollback note below no longer applies: rollback is a redeploy of the
previous git revision to Cloudflare, not a URL switch back to Vercel.

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
- **Rollback.** Before decommissioning (§8): the door URL env is the switch — point it back at
  the Vercel origin and restart the two services. After: check out the previous revision of
  `apps/public-relay` and run the deploy script again (it refuses a dirty tree, so commit or
  stash first).
- **The code-level relay bugs** the move already fixed: the invite secret is out of the URL
  (ADR-0043 §2) and the visitor page no longer carries a name (ADR-0044). The deeper
  seal-the-secret hardening remains deferred (needs on-Pi testing) — see the audit backlog.
