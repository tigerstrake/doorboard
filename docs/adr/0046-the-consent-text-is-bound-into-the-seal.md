# ADR-0046: the consent text is bound into the seal, not just its version

**Status:** Accepted · **Date:** 2026-09-10 · **Amends:** ADR-0016 §3 (fingerprint pinning), ADR-0009 E-7 (consent statement is the single source enrollees see)

## Context

ADR-0009 E-7 makes `docs/policies/consent-statement.md` the one text an enrollee sees, and
bumps its version on any wording change so the `consent_version` recorded at enrollment means
something. ADR-0016 carried that rule onto the phone path: the Pi publishes the statement
beside its public key (`DoorKeyPublication.consent_version` + `consent_text`), the relay serves
both, the phone renders the text verbatim and seals `consent_version` into the manifest, and the
Pi accepts the bundle if the version string matches its own.

The 2026-09-10 relay audit found the gap between those two sentences. The QR fragment pins
only the public key (`k=` is a hash over the SEC1 bytes, E-10). The consent *text* is pinned by
nothing: anyone who can write the relay's `door_key` row — a Cloudflare account compromise, or a
holder of the door's relay device token — can `PUT` the genuine key and genuine fingerprint with
a replaced `consent_text` while keeping `consent_version: "v3"`. The phone passes E-10, shows the
forged wording, seals `v3`, and the Pi records v3 consent. Consent obtained under misrepresented
terms is not consent; the project moved v1 → v2 → v3 precisely so the record is honest.

This is inside the relay's *stated* threat model (ADR-0016 §9: storage or credential compromise
"yields opaque blobs and timing metadata, and nothing else"), not the accepted residual of a
malicious served bundle. It needs closing, and the fix is a contract change, so it needs an ADR
(ADR-0031: additive contract fields are not backward compatible).

## Decision

**1. The manifest carries a hash of the text the enrollee actually saw.** `SealedManifest`
gains a required field:

```
consent_sha256: Base64Url   # SHA-256 over the exact UTF-8 bytes of consent_text as rendered
```

The phone computes it over the `consent_text` string it displayed (no normalisation beyond what
the Pi applies when publishing — the bytes are the file's bytes). It lives inside the AEAD
envelope like the rest of the manifest, so the relay can neither read nor alter it.

**2. The Pi verifies the text, not only the version.** On pickup, `VisiondService` hashes its
canonical statement (the same bytes it publishes in `DoorKeyPublication.consent_text`) and
compares constant-time with `manifest.consent_sha256`. A mismatch is rejected with the existing
opaque outcome `rejected / stale_consent`, and logged under a distinct key
(`consent_text_mismatch`) so an operator can tell a forged text from a client that is merely
behind. The version check stays; both must pass.

**3. The QR fragment pins the text as well as the key.** The door-key fingerprint carried in
the QR (`k=`) becomes

```
fingerprint = base64url( SHA-256( public_key_sec1_bytes || SHA-256(consent_text_bytes) )[:16] )
```

The phone recomputes it from the `public_key` and `consent_text` the relay served and compares
with `k=` before enabling the camera; a substituted text now fails E-10 client-side, before any
photo exists. `DoorKeyPublication.fingerprint` follows the same definition (it is informational —
the phone must compare against the fragment, never against the served field). The
cross-language vector fixture (`tests/fixtures/enrollment-relay/*`) is regenerated, and
`fingerprintFor` (TS) and `relay_seal.py` (Python) change together.

**4. A version bump invalidates open invites by construction.** Because the fingerprint covers
the text, an invite minted under v3 stops passing E-10 the moment the statement changes. That is
the intended behaviour: nobody may enrol under terms the door no longer publishes. Invites are
short-lived (ADR-0016 §5), so the practical cost is "scan a fresh QR".

## Enforcement (additions to the ADR-0016 test specification)

- **E-30** `SealedManifest` requires `consent_sha256`; a manifest without it fails validation on
  both sides (contract test + relay contract-parity test).
- **E-31** Pi: a bundle whose `consent_sha256` does not match the canonical statement is
  rejected `stale_consent` and never enrols, even when `consent_version` matches. Sentinel-tagged
  altered text; the sentinel must not appear anywhere on disk afterwards.
- **E-32** Phone: relay serves the genuine key with an altered `consent_text` → the page lands on
  the blocked screen with no camera access and no `/submit` call (extends P-15).
- **E-33** Fingerprint vectors: the regenerated TS and Python fixtures agree byte-for-byte, and
  a fixture computed under the old (key-only) definition is rejected.

## Consequences

- One additive, required manifest field; one fingerprint redefinition; the phone page, the
  relay's validator, door-visiond and the vector fixtures move together in one change set.
- The relay learns nothing new: the hash rides inside the ciphertext, and the fragment never
  reaches the relay.
- ADR-0016 §9's claim that a compromised relay "cannot enroll anyone" is now also true of
  "cannot alter the terms anyone enrols under". The accepted residual (a malicious served JS
  bundle) is unchanged.
- The backlog item "relay still receives the raw secret at submit" (E-11, ADR-0043 §2) is a
  separate gap and is not addressed here.
