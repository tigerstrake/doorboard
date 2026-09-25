# public-relay — the doorboard's public front door

Public Next.js app serving the doorboard's two internet-facing surfaces, without the door Pi accepting a single inbound connection:

- **Remote enrollment** (`/e/<invite>`) — finish a face enrollment from your phone, from anywhere. [ADR-0016](../../docs/adr/0016-remote-enrollment-relay.md).
- **The visitor page** (`/v/<token>`) — the wallboard QR, which has to work for a stranger whose phone is on cellular and cannot resolve `door.local`. [ADR-0017](../../docs/adr/0017-public-visitor-relay.md).

The two share a deployment and a pattern (outbound-poll courier) but not a security model, and the difference matters:

| | Enrollment | Visitor |
|---|---|---|
| What crosses | AEAD ciphertext sealed to a door-held key | Plaintext: note text, poll choice, ring state |
| Why | Photos and names are biometric-adjacent PII | This content exists to be shown on a hallway wallboard; encrypting it would be theatre |
| What protects it | End-to-end encryption (E-9) | Narrow allow-listed scope, tokenized, rate-limited, short-lived |
| Compromise yields | Opaque blobs and timing | Visitor notes and votes already destined for a public screen |

## The one thing to understand about enrollment

**For enrollment, this deployment is a blind courier.** Photos and the enrollee's name are encrypted on the phone, to a key only the door Pi holds. This app stores the ciphertext for at most 15 minutes and hands it to the Pi when the Pi asks for it.

It holds no key that can open a bundle. Compromising it — the whole D1 database, the device tokens, all of it — yields opaque blobs, timing metadata, and nothing else. It cannot enroll anyone either: the Pi independently verifies every invite against its own database before accepting one (ADR-0016 §4).

## The one thing to understand about the visitor page

**For visitors, the protection is scope, not secrecy.** The relay does see a guestbook note and a poll vote, and that is stated plainly rather than implied. What it must never see is anything *not* already public: no enrolled person's name, no `person_id`, no vision event, no media, no admin or diagnostic state. ADR-0017 §2 is a binding allow-list of the eight snapshot fields, enforced by a parser that rejects unknown fields and by tests on both sides.

The visitor token is never stored either — only `sha256(token)`, so a dump of this store yields no usable credential (E-16).

```text
phone                     this app (Cloudflare)          door Pi
capture + seal  ──POST──▶  ciphertext, 15-min TTL
                                    ▲
                                    └──GET /api/pickup──  outbound poll
                                       POST /api/pickup/ack
```

The Pi always initiates. There is no route here that reaches toward the door, and no admin surface of any kind (E-14).

## Routes

| Route | Who | Purpose |
|---|---|---|
| `GET /` | anyone | Explains the site. No enrollee data. |
| `GET /e/<invite_id>#s=<secret>&k=<fp>` | invited phone | The enrollment flow. The fragment carries the invite secret (`s`) and the door-key fingerprint (`k`); it **never reaches the server** (ADR-0043 §2). The path is just the invite id; the client re-presents the secret in the `X-Doorboard-Invite-Secret` header. |
| `PUT /api/door-key` | door Pi | Publish the sealing public key + canonical consent text. |
| `GET /api/door-key` | invited phone | Fetch them. |
| `PUT /api/invite`, `DELETE /api/invite/<id>` | door Pi | Register / revoke an invite. Only `sha256(secret)` is stored. |
| `GET /api/enroll/<token>` | invited phone | That invite's state: open / consumed / expired / unknown. |
| `POST /api/enroll/<token>/submit` | invited phone | Upload one sealed bundle. |
| `GET /api/status/<bundle_id>` | invited phone | Progress. No name, no biometric data. |
| `GET /api/pickup`, `POST /api/pickup/ack` | door Pi | Collect ciphertext; report the outcome. |
| `GET /api/health` | anyone | `{service, status}` — reachability only. Configuration detail (door checked in, pending count) only with the door-visiond device token. |
| `GET /v/<token>` | visitor at the door | The visitor page: ring status, note, poll vote, deletion request. |
| `PUT /api/visitor/session` | door-api | Push the session snapshot (ADR-0017 §2 allow-list). |
| `GET /api/visitor/<token>` | visitor | Read that snapshot. The token hash is withheld from the response. |
| `POST /api/visitor/<token>/action` | visitor | Queue a note, vote, or deletion request. |
| `GET /api/visitor/pickup`, `POST /api/visitor/pickup/ack` | door-api | Collect queued writes; report outcomes. |

## Environment

Two secrets, neither of which can decrypt anything. In production they are Cloudflare Pages project secrets (`wrangler pages secret put …`), not a file; [.env.example](.env.example) lists them for reference.

| Variable | Purpose |
|---|---|
| `RELAY_DEVICE_TOKEN` | door-visiond → enrollment routes. Must equal `VISIOND_RELAY_DEVICE_TOKEN`. |
| `RELAY_VISITOR_DEVICE_TOKEN` | door-api → visitor routes. Must equal `DOOR_API_VISITOR_RELAY_DEVICE_TOKEN`. Separate credential on purpose (E-17). |

State is the D1 database bound as `DB` in [wrangler.toml](wrangler.toml) (ADR-0043) — a binding, not a connection string. Its schema is the ordered list of files under [migrations/](migrations/), mirrored by `MIGRATIONS` in `lib/d1/schema.ts`; nothing applies one lazily.

Generate the device token:

```bash
openssl rand -base64 36 | tr -d '\n' | tr '+/' '-_'
```

## Local development

From the repo root:

```bash
pnpm install
pnpm --filter @doorboard/public-relay test        # the whole suite, on the real D1 store over node:sqlite
pnpm --filter @doorboard/public-relay typecheck
pnpm --filter @doorboard/public-relay build       # static export → out/
pnpm --filter @doorboard/public-relay dev         # the two pages only, http://localhost:3100
```

There is no Redis or in-memory backend: the tests run the real D1 store over `node:sqlite`, so what they byte-scan is what production holds. The tests are the local loop.

`next dev` serves the pages but not the API — that is a Pages Function (`functions/api/[[route]].ts`), which Next does not run. To exercise it end to end, build and serve the export under wrangler with a local D1: apply `migrations/*.sql` in order with `wrangler d1 execute doorboard-relay --local --file=…`, then `npx wrangler pages dev out`. Point the Pi at that origin (`VISIOND_RELAY_BASE_URL=http://127.0.0.1:8788`); loopback over plain HTTP is permitted for exactly this case, anything else must be `https`.

## Deploying

Cloudflare Pages + Functions + D1, on `door.tigerstrake.com` (ADR-0043). The procedure — D1 database, migrations, secrets, build, deploy, custom domain, verification, rollback — is [docs/runbooks/relay-cloudflare-deploy.md](../../docs/runbooks/relay-cloudflare-deploy.md); `deploy/relay-cloudflare-deploy.sh` automates the non-interactive parts. Two things that are easy to get wrong:

- **Apply every file under `migrations/` in order**, and apply a new one *before* deploying code that expects it. Nothing runs a migration lazily; a missing one fails loudly.
- **The retention sweep runs from the Pi's pickup polls**, not a cron. It is a privacy control, not housekeeping — the runbook explains, and has the manual sweep for a long door outage.

Then on the door Pi:

```bash
VISIOND_RELAY_BASE_URL=https://door.tigerstrake.com
VISIOND_RELAY_DEVICE_TOKEN=<same token>
```

Restart door-visiond. It publishes its public key on startup; `GET /api/health` **with the door-visiond token** (`Authorization: Bearer …`) should then report `door_checked_in: true`. Without a token the route returns reachability only.

## Things that must stay true

Any change to this app is reviewed against these. They are not style preferences.

- **No plaintext enrollment field exists.** `lib/validate.ts` is an allow-list; there is no key a photo or an enrollee's name could arrive under (E-8).
- **No private key, ever.** Not in a secret, not in D1, not in code (E-9).
- **Fingerprint pinning has no bypass.** `assertKeyMatchesFingerprint` gates sealing, and there is no "continue anyway" path (E-10).
- **No admin surface.** Nothing here lists enrollees, mints invites, or calls the Pi (E-14, E-20).
- **The visitor snapshot is the eight fields in ADR-0017 §2, and no others.** An unknown field is a rejection, not something ignored (E-15).
- **No usable visitor credential.** Only `sha256(token)` is stored; this app holds no HMAC secret (E-16).
- **Device scopes stay disjoint.** Every route demands exactly one scope; a route accepting either is a defect (E-17).
- **Content authority stays on the Pi.** Size caps here, sanitisation and moderation there — never a second, weaker validator (E-18).
- **Every row has an expiry, and the sweep enforces it.** Enrollment ciphertext 15 minutes, status 1 hour, invites their expiry plus an hour, visitor snapshots the session plus 5 minutes, queued visitor actions 15 minutes. Reads filter on it; the sweep, driven from the Pi's pickup polls, removes it. On a store without native TTLs that sweep *is* the retention window — a privacy control, not housekeeping.
- **No address at rest.** Rate limits are keyed on a truncated digest of `CF-Connecting-IP`, never the address, and never `X-Forwarded-For` (which Cloudflare appends to rather than replaces).
- **A held bundle id is never overwritten.** A second submit under an existing id is refused; the phone re-seals under a fresh one.

The tests in [tests/relay.test.ts](tests/relay.test.ts), [tests/visitor.test.ts](tests/visitor.test.ts) and [tests/retention.test.ts](tests/retention.test.ts) assert these by byte-scanning what the real D1 store (over `node:sqlite`) actually held, rather than trusting the code to be right.
