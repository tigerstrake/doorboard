# ADR-0009: Enrollment data model and biometric enforcement mechanisms

**Status:** Accepted · **Date:** 2026-07-05 · **Extends:** ADR-0005 (privacy model), ADR-0007 (storage tiers)

This is the binding design T-302 (pipeline), T-303 (identity cache), and T-304 (enrollment UX) implement against. Every ADR-0005 invariant maps below to a **named enforcement mechanism (E-n)** and at least one **specified test (P-n)**. Deviating from this document requires a superseding ADR, not a PR comment.

## 1. Enrollment data model

One SQLite database, WAL mode, at `${SSD_DATA_ROOT}/visiond/enrollment.sqlite` (ADR-0007: SSD, never microSD, never NAS). Opened with `PRAGMA secure_delete=ON` and `PRAGMA journal_mode=WAL` on every connection — secure_delete is load-bearing for deletion semantics (§3).

```sql
CREATE TABLE person (
    person_id        TEXT PRIMARY KEY,   -- opaque: 'prs_' + 22-char base62 from CSPRNG; never derived from name
    display_name     TEXT NOT NULL,      -- presentation only; NEVER a key, NEVER in file paths
    consent_version  TEXT NOT NULL,      -- version tag of docs/policies/consent-statement.md shown at enrollment
    consent_at       TEXT NOT NULL,      -- UTC ISO-8601
    created_at       TEXT NOT NULL
);

CREATE TABLE embedding (
    embedding_id     TEXT PRIMARY KEY,   -- 'emb_' + base62
    person_id        TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    vector           BLOB NOT NULL,      -- float32 array, little-endian, length = model dim
    model_id         TEXT NOT NULL,      -- pinned model identifier + version (T-302 startup check)
    quality          REAL NOT NULL,      -- capture-time quality score
    created_at       TEXT NOT NULL
);

CREATE TABLE profile (
    person_id        TEXT PRIMARY KEY REFERENCES person(person_id) ON DELETE CASCADE,
    profile_id       TEXT NOT NULL UNIQUE,  -- what the ESP32/UI sees; from T-103 effects catalog
    color            TEXT NOT NULL,
    sound            TEXT                -- optional, from effects catalog
);

CREATE TABLE revocation_tombstone (
    person_id        TEXT PRIMARY KEY,   -- opaque id only — no name, no biometrics
    consent_at       TEXT NOT NULL,
    revoked_at       TEXT NOT NULL
);
```

Rules:

- **Raw enrollment images are transient.** Capture → embed → **delete within the same enrollment request**; they live only under `${SSD_DATA_ROOT}/visiond/tmp/enroll-<request_id>/`, which is wiped in a `finally` block and on service startup. No raw face image is ever at rest outside that directory. (Owner may keep their own photos elsewhere; the *system* keeps none.)
- **`person_id` is opaque** (ADR-0005 §8): generated from a CSPRNG at enrollment; display_name is mutable presentation data. Events, caches, ESP32 payloads, and logs use `person_id`/`profile_id` only — `display_name` appears solely in `vision.identity_stable.display_name` (contract-reviewed) and admin surfaces.
- **What crosses the trust boundary to the ESP32** (ADR-0006): `profile_id` + TTL. Never `person_id`, never `display_name`, never anything derived from the vector.
- **No contracts change is needed**: enrollment records are internal to door-visiond; the `/enroll`, `/unenroll` HTTP shapes are defined in §5 and live in the visiond spec, not `packages/contracts`.

## 2. Unknown-embedding lifecycle (the core invariant)

ADR-0005 §2: unknown faces produce nothing durable. Mechanisms:

- **E-1 In-memory-only unknown path.** Per frame: detect → align → embed into an `Embedding` value (see E-2) → match against the in-memory copy of enrolled vectors (loaded once, refreshed on enroll/unenroll). On no-match ≥ threshold, the only permitted outputs are (a) `vision.face_visible` (counts and pixel sizes only) and (b) metric counters. The embedding is dropped at frame-scope end; the pipeline holds no collection keyed by unknown identity — no "recent unknowns" list, no debug ring buffer, no clustering. Any such structure is a review-blocking defect.
- **E-2 No-serialize boundary.** Embedding vectors are wrapped in an `Embedding` class (in door-visiond, not contracts) that: raises `TypeError` from `__json__`-style hooks and is not a dataclass/pydantic model; implements `__repr__`/`__str__` as `Embedding(dim=512, redacted)`; and does not implement `__iter__`/`__reduce__` (pickling raises). Reaching the raw array requires an explicit `.expose_for_matching()` call, which exists so a grep for it enumerates every consumer.
- **E-3 Log redaction filter.** `packages/observability` installs a logging filter on the visiond logger tree that drops/redacts any record whose args contain byte strings > 64 B, float sequences > 16 elements, or keys in `{embedding, vector, face_crop, frame}`. The filter is on by default in every mode including `disabled`; removing it is a review-blocking defect.
- **E-4 Contract firewall.** No event in `packages/contracts` has any field capable of carrying a vector (all payloads `extra="forbid"`, no bytes/array-of-float fields on vision events). Adding one requires an ADR; reviewers reject on sight.

## 3. Deletion semantics that actually delete

Unenroll (`POST /unenroll {person_id}`) executes, in order, one transaction then three propagations:

1. **DB purge:** `DELETE FROM person WHERE person_id=?` (cascades to embedding+profile); insert `revocation_tombstone`; commit; then `PRAGMA wal_checkpoint(TRUNCATE)`. With `secure_delete=ON`, freed pages are zeroed and the truncated WAL leaves no stale copies. (**E-5 Purge-and-checkpoint**)
2. **Cache flush:** if `current_visitor` matches, expire it immediately and emit `vision.identity_expired`. (T-303)
3. **ESP32 clear:** `door.profile_clear(reason="admin")` sent with ack tracking; heartbeat `cached_profile_id` confirms null. (T-303)
4. **Archive purge hook:** events already emitted (e.g. `identity_stable` with display_name) may exist in the NUC archive. The control plane (T-501) **must expose a person_id-keyed purge** (`DELETE /people/{person_id}/events` semantics) which unenroll calls best-effort and queues durably if the NUC is down (retry via door-sync). Unenroll never blocks on the NUC (critical-path rule) — local deletion is immediate, remote purge is eventually-consistent and visible in the admin UI until confirmed.

The tombstone (opaque id + timestamps, nothing else) exists so "was this person ever enrolled / when was consent revoked" is answerable without retaining anything biometric or nominal.

## 4. Privacy mode

- **E-6 Capture-layer kill switch.** Privacy mode disables recognition **at the frame source**: the recognition capture loop stops delivering frames to detect/embed (not merely suppressing match results). `vision.pipeline_status` reports `mode="disabled"`; `vision.privacy_mode_changed{enabled, changed_by}` is emitted. Button, UI, bell recording-by-request, and door-media streaming are untouched (ADR-0005 §4).
- Enabling privacy mode also: flushes `current_visitor`, sends `door.profile_clear(reason="privacy_mode")`, and rejects `/enroll` with 409 while active (enrollment requires the pipeline).
- The flag persists in visiond's config store on the SSD and is restored on boot **before** the first frame is captured. Sources: admin UI, physical toggle (feature-flagged GPIO, later), config file.

## 5. Admin API shapes (visiond-local, admin-authenticated per trust model)

```
POST /enroll    {display_name, consent_version, consent_confirmed: true, images: [multipart], profile: {profile_id, color, sound?}}
                → 201 {person_id, embeddings_created, quality: [...]} | 409 privacy_mode | 422 quality-too-low
POST /unenroll  {person_id} → 200 {deleted: true, archive_purge: "confirmed"|"queued"}
GET  /current-visitor → 200 {person_id, display_name, expires_at_monotonic_ms} | 204
POST /privacy-mode {enabled, changed_by} → 200
```

`consent_confirmed` must be literal `true` and `consent_version` must match the current version of `docs/policies/consent-statement.md` — the single consent-language source T-304's CLI and admin UI both render (**E-7 Single consent source**). Stale version → 409 with the current version in the body.

## 6. Stolen-Pi threat model

**What a thief holds** (door Pi + SSD, per ADR-0007): the enrollment DB (embeddings + display names of a small consented set), recent unsynced clips, session/social SQLite DBs, thumbnails, and limited-scope revocable tokens. **Not held:** NAS archive, admin credentials, HA admin, long-term history.

Embedding vectors are not directly reversible to photographs, but treat them as biometric identifiers: they allow re-identification against another camera running the same model. Combined with display names, that is real PII.

Mitigation options considered:

| Option | Protects against | Cost |
|---|---|---|
| A. Nothing (rely on small consented set + revocation) | — | zero |
| B. LUKS on the enrollment partition, key on microSD | SSD-only theft | ~zero; useless if whole Pi is taken (the realistic case) |
| C. LUKS, key fetched from NUC at boot (network-bound), cached only in RAM | whole-Pi theft (post power-off) | after power loss, recognition stays `disabled` until the NUC is reachable once; **button/UI unaffected** (visiond degrades per its normal path) |
| D. Full-disk SSD encryption, network key | C + clips/DBs | same as C plus recording unavailable until unlock — violates "recording works standalone" |

**Recommendation: Option C**, scoped to a separate LUKS volume holding only `visiond/` (enrollment DB + tmp). It converts the stolen-Pi outcome for biometrics from "exposed" to "ciphertext without key," and its only degradation (no recognition after cold boot until NUC seen once) is already an accepted failure mode — recognition is personalization, never authorization (ADR-0005 §3), so nothing breaks. Door interaction, recording, and sync run unencrypted on the SSD as today (their theft exposure is bounded and already accepted in ADR-0007). Implementation lands with T-302 deployment scripts (systemd unit ordering: unlock → visiond); a `visiond_enrollment_locked` health state covers the pre-unlock window. Pi 5 has no TPM/secure element — key-on-NUC is the strongest practical binding; we accept that an attacker who steals the Pi *while powered and unlocked* can read the DB (as they could with any option short of secure-element hardware).

## 7. Test specification (binding for T-302/T-303)

Non-persistence and log-cleanliness must be **proven by inspection, not by trust**. The sentinel technique: tests enroll/emit vectors containing a recognizable 32-byte marker (`b"DOORBOARD_SENTINEL_" + suffix`) and then search raw bytes of files.

| ID | Test (exact behavior) | Task |
|---|---|---|
| P-1 | `test_unknown_embedding_never_touches_disk` — run pipeline in mock/single-camera mode over a scripted sequence of unknown faces whose embeddings contain a sentinel; after N frames: byte-scan every file under `${SSD_DATA_ROOT}` (including WAL/tmp) for the sentinel → absent; enrollment DB row counts unchanged; no new files outside an explicit allowlist. | T-302 |
| P-2 | `test_unknown_face_emits_face_visible_only` — same run: exactly `vision.face_visible` events, zero `identity_*`, zero payload fields beyond the contract. | T-302 |
| P-3 | `test_embedding_type_refuses_serialization` — `json.dumps(embedding)` raises; `pickle.dumps` raises; `repr()`/`str()`/`f"{}"` contain no digits of the vector. | T-302 |
| P-4 | `test_logs_are_biometric_clean` — capture ALL log output at DEBUG during P-1's run; assert: sentinel absent, no float-sequence > 16 elements, no denylist keys, no base64 blob > 64 B. Also wired as a CI grep over the full simulator-scenario log soak. | T-302 |
| P-5 | `test_unenroll_purges_bytes` — enroll a person whose vectors carry a sentinel; unenroll; byte-scan `enrollment.sqlite` **and its WAL/SHM** for the sentinel → absent (proves secure_delete + checkpoint, not just row deletion); tombstone row exists with no name. | T-302 |
| P-6 | `test_unenroll_propagates` — unenroll while person is the cached current visitor → `identity_expired` emitted, cache empty, `door.profile_clear(admin)` acked, next heartbeat `cached_profile_id` null; archive purge queued when NUC-sim is down and drains on recovery. | T-303 |
| P-7 | `test_privacy_mode_kills_capture_not_door` — enable privacy mode: frame counter stops advancing (capture-layer, not result suppression), `profile_clear(privacy_mode)` sent, `/enroll` → 409; button press through simulator still completes the full session flow. | T-302/303 |
| P-8 | `test_privacy_mode_survives_restart` — enable, restart service, assert no frame is captured before the persisted flag is applied. | T-302 |
| P-9 | `test_enroll_tmp_is_transient` — after a successful and after a failed (exception-injected) enrollment, `${SSD_DATA_ROOT}/visiond/tmp/` is empty; also emptied by service startup. | T-302 |
| P-10 | `test_cooldown_suppresses_reidentify` — same person continuously visible: exactly one `identity_stable` per 30 s window. | T-302 |
| P-11 | `test_no_identity_in_authorization_path` — grep-level CI check: nothing under `apps/door-api` imports match results for any gating decision; identity events influence greeting/display fields only (assertion on session state machine inputs). | T-303/T-401 |

## 8. PR template addition

Add one line to the existing privacy checklist: `- [ ] No new serialization of embedding/frame data (grep: json.dumps/pickle/tobytes near Embedding; expose_for_matching call sites enumerated and justified)`.

## Invariant → mechanism → proof map (acceptance)

| ADR-0005 invariant | Mechanism | Proof |
|---|---|---|
| §1 opt-in enrollment, revocation | §1 model, E-7 | P-5, P-9, T-304 CI flow |
| §2 unknowns produce nothing durable | E-1, E-2, E-3, E-4 | P-1..P-4 |
| §3 never authorization | contract review + §7 | P-11 |
| §4 privacy mode | E-6 | P-7, P-8 |
| §6 deletable | E-5 + §3 propagation | P-5, P-6 |
| §8 opaque IDs | §1 model rules | schema + P-4 |
| Consequences: log review | E-3 | P-4 |

## Consequences

- T-302/T-303/T-304 briefs updated (this PR) to reference the sections above; their acceptance criteria inherit P-1…P-11 by ID.
- T-501's brief gains the person_id-keyed archive purge endpoint (§3.4).
- Deployment (T-302) adds the LUKS enrollment volume + unlock unit; T-190/T-390 drills verify cold-boot behavior.
- `docs/policies/consent-statement.md` is created by this task as v1 and is the single consent source (E-7); changing it bumps `consent_version`.
