# ADR-0016: Remote enrollment via a blind-courier relay

**Status:** Accepted · **Date:** 2026-08-05 · **Amends:** ADR-0009 §1 and §6 · **Extends:** ADR-0005 §1, ADR-0007 (storage tiers)

This ADR authorizes something ADR-0009 forbade: face images captured off-premises and carried to the door Pi over the public internet. It is binding on T-306. Every new exposure below maps to a named enforcement mechanism (**E-n**, continuing ADR-0009's numbering) and at least one specified test (**P-n**). Deviating requires a superseding ADR.

## Context

The owner requires enrollment to work from a phone that is **not** on the house LAN, reached by a QR code that resolves anywhere, served by a site that is up even when the house is not. Two existing commitments block that:

- **ADR-0009 §1**: raw enrollment images live only under `${SSD_DATA_ROOT}/visiond/tmp/enroll-<request_id>/` and are wiped in the same request. No raw face image is ever at rest outside that directory.
- **Consent statement v1** promises enrollees that their data "never leaves the local network."

The system has no public ingress of any kind today, by design (ARCHITECTURE.md §2). Exposing door-visiond — the holder of the enrollment DB and the Hailo — to the internet is not an option worth discussing: it is the highest-value, lowest-trust host in the topology (Medium/low trust, physically stealable).

The question is therefore not "how do we let the internet reach the Pi" but "how do we let a photo travel from a phone to the Pi without any intermediary being able to read it, and without the Pi accepting inbound connections."

## Decision

A standalone Next.js application on Vercel (`apps/public-relay`) acts as a **blind courier**. It moves sealed bytes and knows nothing about their contents.

```text
phone (anywhere)                    vercel relay                    door pi
──────────────────                  ─────────────                   ───────
capture N photos in-browser
build manifest {name, consent,
  profile} + photos
   │
   ├─ fetch door public key ────────► GET /api/door-key
   │   verify fingerprint against
   │   the #k= fragment in the QR
   │
   ├─ ECDH(ephemeral, door_pub)
   │   → HKDF → AES-256-GCM
   │   seal manifest AND photos
   │
   └─ POST sealed bundle ───────────► KV, 15-min TTL
                                      ciphertext only
                                            ▲
                                            │ outbound poll (no inbound port,
                                            │ works behind NAT/CGNAT)
                                      GET /api/pickup ◄──────────── relay client
                                                                          │
                                                                    decrypt in RAM
                                                                    → existing enroll()
                                                                    → Hailo embed
                                                                    → wipe tmp + bundle
                                      POST /api/pickup/ack ◄─────────────┘
                                      deletes ciphertext
```

Enrollment remains **opt-in and admin-authorized** (ADR-0005 §1): a remote enrollment is possible only against a single-use invite that the household admin minted on the Pi.

### 1. What each party can see

| Party | Sees | Never sees |
|---|---|---|
| Phone browser | Everything it captured (the enrollee's own face) | Other enrollees, the enrollment DB |
| Vercel relay | `bundle_id`, `invite_id`, `door_key_id`, ephemeral public key, ciphertext, byte lengths, timestamps | Face images, display name, profile, embeddings, `person_id` — all sealed or never sent |
| Door Pi | Everything, transiently; embeddings durably | — |
| ESP32 | `profile_id` + TTL (unchanged, ADR-0009 §1) | — |

The relay never receives a display name. **The name is inside the sealed manifest**, not an upload field — so a subpoena, a KV dump, or a Vercel-side compromise yields opaque identifiers and AEAD ciphertext, not "who enrolled at this house."

### 2. Sealed bundle format

Ephemeral-static ECIES. Chosen for WebCrypto support breadth: P-256 ECDH is available in every current browser, whereas X25519 in WebCrypto is still uneven across Safari versions.

```text
door static key:  P-256, generated on the Pi, private key never leaves the
                  encrypted enrollment volume
per bundle:       ephemeral P-256 keypair generated in the browser, discarded
                  after sealing (never persisted, never sent beyond its pubkey)

shared  = ECDH(eph_priv, door_pub)
key     = HKDF-SHA256(shared, salt=bundle_salt(32B random),
                      info="doorboard/enroll-relay/v1|" || door_key_id || "|" || bundle_id)
          → 32 bytes
each item i (manifest is item 0, photos are items 1..N):
  nonce_i = 12 random bytes (never reused under `key`)
  ct_i    = AES-256-GCM(key, nonce_i, plaintext_i,
                        aad = bundle_id || ":" || door_key_id || ":" || i)
```

Binding `bundle_id`, `door_key_id`, and the item index into the AAD and the HKDF `info` means a bundle cannot be replayed against a different door or key, and items cannot be reordered or transplanted between bundles.

The manifest (item 0) carries `{display_name, consent_version, consent_confirmed, profile:{profile_id, color, sound?}, captured_at, image_count}`. Item count and per-item lengths are visible to the relay; nothing else is.

### 3. Key management and fingerprint pinning

- The door keypair is generated on first use and stored at `${enrollment_root}/relay/door_key.json` — **on the LUKS enrollment volume** (ADR-0009 §6 option C), so a stolen powered-off Pi yields ciphertext without a key. While that volume is locked, remote enrollment is unavailable, consistent with the existing `visiond_enrollment_locked` health state.
- The Pi publishes only the **public** key to the relay, with an opaque `door_key_id`.
- **The QR code the Pi generates embeds the key fingerprint in the URL fragment** (`https://…/e/<token>#k=<fp>`). Fragments are never sent to the server. The web app refuses to seal if the key served by `/api/door-key` does not match the fingerprint from the QR. A relay that swaps in its own key to become able to decrypt is therefore detected by any client that arrived via a genuine Pi-generated QR.
- Rotation: generating a new keypair publishes a new `door_key_id`. In-flight bundles sealed to the old key still decrypt — the Pi keeps retired private keys until the relay TTL has certainly expired, then deletes them.

### 4. Invite authorization — verified twice, independently

An invite is minted **on the Pi** by an admin (kiosk admin panel or CLI). Token form is `<invite_id>.<secret>` where `secret` is 32 CSPRNG bytes, base64url. The Pi stores only `sha256(secret)`.

1. **Relay check (spam control).** At mint time the Pi registers `{invite_id, sha256(secret), expires_at}` outbound to the relay. The relay rejects a submission whose presented secret does not hash to the stored value, or whose invite is expired or already consumed. This exists so the relay is not an open ciphertext dump.
2. **Pi check (authorization).** On pickup the Pi **re-verifies** `invite_id` against its own SQLite: known, unexpired, unconsumed, and `sha256(secret)` matching. Only then does it enroll, and it marks the invite consumed in the same transaction.

Because the relay holds only hashes and the Pi's own table is authoritative, **a fully compromised relay still cannot cause an enrollment.** It can deny service, and it can observe metadata. It cannot forge an invite the Pi will honor, and it cannot read a bundle.

### 5. Relay data model and retention

Vercel KV (Upstash Redis), native TTL on every key. No other durable store; no logs of request bodies.

| Key | Value | TTL |
|---|---|---|
| `invite:<invite_id>` | secret hash, expiry, consumed flag | invite expiry + 1 h |
| `bundle:<bundle_id>` | sealed bundle (ciphertext) | **15 min** |
| `status:<bundle_id>` | `pending` \| `collected` \| `enrolled` \| `failed:<reason>` | 1 h |
| `ratelimit:*` | counters | 1 h |

Ciphertext is deleted on ack, and expires on its own if the Pi never collects it. Status outlives the ciphertext deliberately so the phone can be told what happened; it carries no name and no biometric data.

### 6. Pi-side collection and the critical path

The relay client is an `asyncio` task in door-visiond alongside the existing purge outbox loop. Binding constraints:

- **It is never in the door interaction path.** Button → ESP32 → local UI is untouched. A hung or unreachable relay cannot slow a bell press; the poll runs on its own task with a hard HTTP timeout.
- **Outbound only.** No inbound port, no tunnel, no port forward. Works behind NAT and CGNAT.
- Relay unreachable → bounded exponential backoff, a warning counter in `/metrics`, and `relay_status: "degraded"` in `/health`. The service stays `ok`: remote enrollment is a convenience, and per ADR-0005 §3 recognition is personalization, never authorization, so nothing security-relevant degrades.
- Privacy mode active or enrollment volume locked → the poller does not collect at all (matching `/enroll`'s 409/503 behavior). Bundles expire in the relay untouched.

### 7. Enforcement mechanisms

- **E-8 Seal-before-upload.** The web app has no code path that transmits an unsealed image or a display name. Sealing happens in the browser before the first byte leaves; the submit handler accepts only an already-sealed bundle object. The relay's request schema has no field capable of holding plaintext image data or a name — an attempt to add one is a review-blocking defect.
- **E-9 Relay key-poverty.** The relay never holds a private key, and no Vercel environment variable is a decryption key. Verified by inspection: the only secrets in the relay's env are the device bearer token and KV credentials.
- **E-10 Fingerprint pinning.** Sealing is refused unless the fetched door key matches the fingerprint carried in the QR fragment (§3). No "continue anyway" affordance exists.
- **E-11 Double invite verification.** §4. The Pi's local invite table is authoritative; relay assertions are advisory. Enrollment and invite-consumption are one transaction, so an invite cannot be spent twice by concurrent pickups.
- **E-12 Transient plaintext.** Decrypted images are handed straight to the existing `VisiondService.enroll()`, inheriting ADR-0009 §1's tmp-dir-and-`finally`-wipe. Decrypted bytes are never written anywhere else, never logged, and the sealed bundle is dropped from memory after decrypt. Retired private keys are deleted once the relay TTL has lapsed.
- **E-13 Contract firewall extension.** Relay wire shapes live in `packages/contracts` (they cross a trust boundary between two independently deployed artifacts, so ADR-0009 §1's "visiond-local shapes" carve-out does not apply). Ciphertext is typed as base64url `str`. No relay model has a bytes field, a float-sequence field, or any field capable of carrying a vector — ADR-0009 E-4 continues to hold, extended to non-event API models.
- **E-14 No admin surface on the relay.** The relay exposes no route that can list enrollees, mint invites, read the enrollment DB, or reach the Pi. Invites arrive from the Pi outbound; the Pi initiates every other exchange. The relay cannot ask the Pi for anything.

## 8. Test specification (binding for T-306)

| ID | Test (exact behavior) | Where |
|---|---|---|
| P-12 | `test_sealed_bundle_round_trip` — seal with the TS implementation's vectors, open with the Python implementation and vice versa; corrupt one AAD byte, one nonce byte, one ciphertext byte → each raises, none partially decrypts. | contracts/visiond |
| P-13 | `test_relay_never_sees_plaintext` — drive a full submit through the relay's own handlers with a sentinel-bearing image and sentinel display name; byte-scan every value written to the KV double, plus captured request/response logs → sentinels absent. | public-relay |
| P-14 | `test_invite_single_use_and_expiry` — a consumed invite, an expired invite, a hash-mismatched secret, and an `invite_id` the Pi never issued are each rejected by the Pi; concurrent pickups of one invite enroll exactly once. | visiond |
| P-15 | `test_fingerprint_mismatch_refuses_seal` — relay serves a substituted door key; the client refuses to seal and surfaces a tamper error; no upload occurs. | public-relay |
| P-16 | `test_remote_enroll_plaintext_is_transient` — after a successful and an exception-injected remote enrollment, byte-scan everything under `${SSD_DATA_ROOT}` (including WAL/tmp) for an image sentinel → absent; enrollment succeeded in the success case. Extends ADR-0009 P-9 to the relay path. | visiond |
| P-17 | `test_relay_outage_never_blocks_door` — relay returns 500s / hangs past the timeout for the whole run: button→session flow completes normally through the simulator, `/health` reports the service `ok` with `relay_status: "degraded"`, backoff is bounded. | visiond |
| P-18 | `test_relay_poller_respects_privacy_and_lock` — privacy mode on, and separately the enrollment volume locked: pending bundles are not collected and no plaintext is produced. | visiond |
| P-19 | `test_relay_logs_are_clean` — all relay-path logs at DEBUG contain no sentinel, no display name, no base64 blob > 64 B (ADR-0009 E-3 filter must cover the new logger). | visiond |

## 9. Threat model changes

**Unchanged: stolen Pi.** ADR-0009 §6 option C still applies; the relay private key lives on the same LUKS volume and is protected identically.

**New: compromised relay (storage/credential level).** An attacker with KV access or the device token gets: metadata (when enrollments happen, how many photos, byte lengths) and AEAD ciphertext they cannot open. They can deny service and can replay a bundle — which the Pi rejects via single-use invite consumption (E-11). They cannot enroll anyone and cannot learn a name.

**New, accepted: a fully compromised Vercel deployment can serve malicious JavaScript.** Any browser-delivered cryptography carries this: an attacker who can change the served bundle can make it exfiltrate plaintext before sealing, defeating E-10 by removing the check. Nothing short of a native app or a signed extension closes it. We accept it because: the blast radius is limited to people who enroll *while* the deployment is compromised (existing enrollees are unaffected — the relay holds nothing about them); the at-door kiosk flow remains a zero-cloud-trust alternative and is the recommended path when convenience is not needed; and deployment access is protected by the owner's Vercel account with the source in git for comparison. This is stated plainly in the consent statement so enrollees can choose the at-door path instead.

**Not changed:** recognition is still never authorization. No relay input can influence any access decision, because no access decision consumes identity at all (ADR-0009 P-11).

## 10. Consent statement bumps to v2

Consent v1 states data "never leaves the local network." For a remote enrollment that is false, and E-7 makes the statement the single source enrollees actually see. `docs/policies/consent-statement.md` therefore goes to **v2**, adding, in plain language: that choosing the phone path sends *encrypted* photos through a relay that cannot read them; that they are deleted after processing; that the at-door path sends nothing anywhere; and that photos are deleted immediately either way.

Existing v1 enrollments keep `consent_version: "v1"` — that is the historical record and must not be rewritten. The stale-consent 409 path (ADR-0009 §5) handles clients still submitting v1.

## Consequences

- door-visiond gains a `cryptography` dependency (P-256 ECDH, HKDF, AES-GCM). Standard, audited, already the de facto Python choice; ADR-0003 is extended by this line.
- door-visiond gains outbound internet egress for the first time, to exactly one configured origin. Deployment docs and the security checklist must say so.
- `apps/public-relay` is the first artifact deployed outside the house. It holds no secret capable of reading user data, so its compromise is contained as described in §9.
- The relay is optional: unset `VISIOND_RELAY_BASE_URL` and the poller never starts. The at-door flow is unaffected and remains the default.
- ADR-0009 §1's "no contracts change is needed" no longer holds for the relay path; E-13 defines the replacement rule.
- Rate limits and the absence of an admin surface on the relay (E-14) are review-blocking invariants for any future change to `apps/public-relay`.
