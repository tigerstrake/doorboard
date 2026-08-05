# enroll-web — remote enrollment relay

Public Next.js app that lets someone finish a face enrollment from their phone, from anywhere, without the door Pi accepting a single inbound connection.

Binding design: [ADR-0016](../../docs/adr/0016-remote-enrollment-relay.md). This README explains how to run and deploy it; the ADR explains why it is shaped this way and what it is allowed to know.

## The one thing to understand

**This deployment is a blind courier.** Photos and the enrollee's name are encrypted on the phone, to a key only the door Pi holds. This app stores the ciphertext for at most 15 minutes and hands it to the Pi when the Pi asks for it.

It holds no key that can open a bundle. Compromising it — the whole KV store, the device token, all of it — yields opaque blobs, timing metadata, and nothing else. It cannot enroll anyone either: the Pi independently verifies every invite against its own database before accepting one (ADR-0016 §4).

```text
phone                     this app (Vercel)              door Pi
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
| `GET /e/<invite_id>.<secret>#k=<fp>` | invited phone | The enrollment flow. The `#k=` fragment is the door key fingerprint and **never reaches the server**. |
| `PUT /api/door-key` | door Pi | Publish the sealing public key + canonical consent text. |
| `GET /api/door-key` | invited phone | Fetch them. |
| `PUT /api/invite`, `DELETE /api/invite/<id>` | door Pi | Register / revoke an invite. Only `sha256(secret)` is stored. |
| `GET /api/enroll/<token>` | invited phone | That invite's state: open / consumed / expired / unknown. |
| `POST /api/enroll/<token>/submit` | invited phone | Upload one sealed bundle. |
| `GET /api/status/<bundle_id>` | invited phone | Progress. No name, no biometric data. |
| `GET /api/pickup`, `POST /api/pickup/ack` | door Pi | Collect ciphertext; report the outcome. |
| `GET /api/health` | anyone | Configuration and reachability only. |

## Environment

See [.env.example](.env.example). Two secrets, neither of which can decrypt anything:

| Variable | Purpose |
|---|---|
| `RELAY_DEVICE_TOKEN` | Bearer token the door Pi authenticates with. Must equal `VISIOND_RELAY_DEVICE_TOKEN` on the Pi. |
| `UPSTASH_REDIS_REST_URL` / `_TOKEN` | Upstash Redis. The Vercel marketplace integration injects `KV_REST_API_URL` / `KV_REST_API_TOKEN` instead; both pairs are accepted. |

Generate the device token:

```bash
openssl rand -base64 36 | tr -d '\n' | tr '+/' '-_'
```

## Local development

From the repo root:

```bash
pnpm install
pnpm --filter @doorboard/enroll-web dev     # http://localhost:3100
pnpm --filter @doorboard/enroll-web test
```

A local run still needs an Upstash database (the free tier is fine) — there is no in-memory fallback, deliberately, so local behaviour matches production.

Point the Pi at it with `VISIOND_RELAY_BASE_URL=http://127.0.0.1:3100`. Loopback over plain HTTP is permitted for exactly this case; anything else must be `https`.

## Deploying

```bash
pnpm dlx vercel@latest login
pnpm dlx vercel@latest link            # run from apps/enroll-web
pnpm dlx vercel@latest env add RELAY_DEVICE_TOKEN production
# add the Upstash integration in the Vercel dashboard, or set UPSTASH_* manually
pnpm dlx vercel@latest --prod
```

Because this lives in a pnpm workspace, the Vercel project's **Root Directory** must be `apps/enroll-web` while the build runs from the repo root — `next.config.ts` already sets `turbopack.root` and `outputFileTracingRoot` accordingly.

Then on the door Pi:

```bash
VISIOND_RELAY_BASE_URL=https://<your-deployment>
VISIOND_RELAY_DEVICE_TOKEN=<same token>
```

Restart door-visiond. It publishes its public key on startup, and `GET /api/health` should report `door_checked_in: true`.

## Things that must stay true

Any change to this app is reviewed against these. They are not style preferences.

- **No plaintext field exists.** `lib/validate.ts` is an allow-list; there is no key a photo or a name could arrive under (E-8).
- **No private key, ever.** Not in env, not in KV, not in code (E-9).
- **Fingerprint pinning has no bypass.** `assertKeyMatchesFingerprint` gates sealing, and there is no "continue anyway" path (E-10).
- **No admin surface.** Nothing here lists enrollees, mints invites, or calls the Pi (E-14).
- **Every key has a TTL.** Ciphertext 15 minutes, status 1 hour, invites their own expiry plus an hour (ADR-0016 §5).

The tests in [tests/relay.test.ts](tests/relay.test.ts) assert these by byte-scanning what the store actually held, rather than trusting the code to be right.
