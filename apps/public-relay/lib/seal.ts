/**
 * Browser-side sealing (ADR-0016 §2, mechanism E-8).
 *
 * This runs on the enrollee's phone and is the only place their photos exist in
 * the clear outside the door Pi. It must stay a faithful mirror of
 * `apps/door-visiond/src/door_visiond/relay_seal.py` (which opens what this
 * seals) and of the test-side implementation in
 * `apps/door-visiond/tests/relay_helpers.py` — the round-trip tests (P-12) fail
 * if the three drift.
 *
 * Two rules hold here without exception:
 *
 *  1. Nothing leaves this module unsealed. The manifest — display name, invite
 *     secret, profile — is item 0 of the AEAD envelope, not an upload field, so
 *     the relay cannot see it even in principle (§1).
 *  2. Sealing is refused unless the door key matches the fingerprint carried in
 *     the QR's URL fragment (E-10). There is no override.
 */
import type { SealedBundle, SealedItem, SealedManifest } from "./contracts";
import { SEAL_INFO_PREFIX, SEAL_SUITE } from "./contracts";

const CURVE = { name: "ECDH", namedCurve: "P-256" } as const;
const NONCE_BYTES = 12;
const SALT_BYTES = 32;

export class KeyFingerprintMismatch extends Error {
  constructor() {
    super(
      "The door's key does not match the code you scanned. " +
        "Nothing has been sent. Ask for a fresh QR code, or enrol at the door instead.",
    );
    this.name = "KeyFingerprintMismatch";
  }
}

export function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function base64UrlDecode(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (value.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** First 16 bytes of SHA-256 over the SEC1 point, base64url (ADR-0016 §3). */
export async function fingerprintFor(publicKeyBytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", publicKeyBytes as BufferSource);
  return base64UrlEncode(new Uint8Array(digest).slice(0, 16));
}

/**
 * Verify the served door key against the fingerprint from the QR fragment.
 *
 * Throws {@link KeyFingerprintMismatch} on any disagreement. A caller that
 * catches this and continues anyway is a review-blocking defect.
 */
export async function assertKeyMatchesFingerprint(
  publicKey: string,
  expectedFingerprint: string,
): Promise<void> {
  const actual = await fingerprintFor(base64UrlDecode(publicKey));
  if (actual.length !== expectedFingerprint.length || actual !== expectedFingerprint) {
    throw new KeyFingerprintMismatch();
  }
}

export interface SealInput {
  doorPublicKey: string;
  doorKeyId: string;
  inviteId: string;
  bundleId: string;
  manifest: SealedManifest;
  images: Uint8Array[];
}

/**
 * Seal a manifest plus photos to the door's public key.
 *
 * Ephemeral-static ECIES: a fresh P-256 keypair per bundle, ECDH against the
 * door's static key, HKDF-SHA256 to an AES-256-GCM key. `bundle_id`,
 * `door_key_id`, and the item index are bound into both the HKDF `info` and each
 * item's AAD, so a bundle cannot be replayed against another door or key and its
 * items cannot be reordered or transplanted.
 */
export async function sealBundle(input: SealInput): Promise<SealedBundle> {
  const { doorPublicKey, doorKeyId, inviteId, bundleId, manifest, images } = input;
  if (images.length === 0) throw new Error("at least one photo is required");

  const doorKey = await crypto.subtle.importKey(
    "raw",
    base64UrlDecode(doorPublicKey) as BufferSource,
    CURVE,
    false,
    [],
  );
  const ephemeral = await crypto.subtle.generateKey(CURVE, true, ["deriveBits"]);

  const sharedBits = await crypto.subtle.deriveBits(
    { name: "ECDH", public: doorKey },
    ephemeral.privateKey,
    256,
  );
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const hkdfKey = await crypto.subtle.importKey("raw", sharedBits, "HKDF", false, ["deriveBits"]);
  const derived = await crypto.subtle.deriveBits(
    {
      name: "HKDF",
      hash: "SHA-256",
      salt: salt as BufferSource,
      info: new TextEncoder().encode(`${SEAL_INFO_PREFIX}|${doorKeyId}|${bundleId}`) as BufferSource,
    },
    hkdfKey,
    256,
  );
  const aesKey = await crypto.subtle.importKey("raw", derived, "AES-GCM", false, ["encrypt"]);

  // Item 0 is the manifest; items 1..N are the photos, in capture order.
  const plaintexts: Uint8Array[] = [
    new TextEncoder().encode(JSON.stringify(manifest)),
    ...images,
  ];

  const items: SealedItem[] = [];
  for (let index = 0; index < plaintexts.length; index += 1) {
    const nonce = crypto.getRandomValues(new Uint8Array(NONCE_BYTES));
    const aad = new TextEncoder().encode(`${bundleId}:${doorKeyId}:${index}`);
    const ciphertext = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv: nonce as BufferSource, additionalData: aad as BufferSource, tagLength: 128 },
      aesKey,
      plaintexts[index] as BufferSource,
    );
    items.push({
      index,
      nonce: base64UrlEncode(nonce),
      ciphertext: base64UrlEncode(new Uint8Array(ciphertext)),
    });
  }

  const ephemeralPublic = await crypto.subtle.exportKey("raw", ephemeral.publicKey);

  return {
    v: 1,
    suite: SEAL_SUITE,
    bundle_id: bundleId,
    invite_id: inviteId,
    door_key_id: doorKeyId,
    ephemeral_public_key: base64UrlEncode(new Uint8Array(ephemeralPublic)),
    salt: base64UrlEncode(salt),
    items,
  };
}

/** Opaque bundle id: `bnd_` + 22 base62 characters, matching the Pi's format. */
export function newBundleId(): string {
  const alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const random = crypto.getRandomValues(new Uint8Array(22));
  let body = "";
  for (const byte of random) body += alphabet[byte % alphabet.length];
  return `bnd_${body}`;
}
