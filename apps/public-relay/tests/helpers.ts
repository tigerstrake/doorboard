/**
 * Test fixtures: a deterministic door keypair and real sealed bundles.
 *
 * The sealing here goes through the production `lib/seal.ts`, so these tests
 * exercise the same code the phone runs, using Node's WebCrypto.
 */
import { webcrypto } from "node:crypto";

import type { DoorKeyPublication, SealedBundle } from "@/lib/contracts";
import { SEAL_SUITE } from "@/lib/contracts";
import { base64UrlEncode, fingerprintFor, newBundleId, sealBundle } from "@/lib/seal";

export const DEVICE_TOKEN = "test-device-token-value";
/** Recognizable markers for the byte scans (mirrors ADR-0009 §7's technique). */
export const NAME_SENTINEL = "DOORBOARD_SENTINEL_NAME";
export const IMAGE_SENTINEL = "DOORBOARD_SENTINEL_IMAGE";

export const b64url = base64UrlEncode;

export interface FakeDoorKey {
  publication: DoorKeyPublication;
  publicKeyBytes: Uint8Array;
  privateKey: CryptoKey;
}

const cache = new Map<string, FakeDoorKey>();

/**
 * A door keypair, cached per label so repeated calls in one test return the same
 * key (the real Pi's key is stable across a session).
 */
export async function fakeDoorKey(label = "primary"): Promise<FakeDoorKey> {
  const existing = cache.get(label);
  if (existing) return existing;

  const pair = (await webcrypto.subtle.generateKey(
    { name: "ECDH", namedCurve: "P-256" },
    true,
    ["deriveBits"],
  )) as CryptoKeyPair;
  const raw = new Uint8Array(await webcrypto.subtle.exportKey("raw", pair.publicKey));
  const fingerprint = await fingerprintFor(raw);

  const key: FakeDoorKey = {
    publicKeyBytes: raw,
    privateKey: pair.privateKey,
    publication: {
      door_key_id: `dky_${label.padEnd(22, "0").slice(0, 22)}`,
      suite: SEAL_SUITE,
      public_key: base64UrlEncode(raw),
      fingerprint,
      consent_version: "v2",
      consent_text: "# Face-recognition consent statement\n\n**Version: v2**\n",
      published_at: new Date().toISOString(),
    },
  };
  cache.set(label, key);
  return key;
}

/** Seal a bundle carrying the sentinels, through the production seal path. */
export async function makeSealedBundle(options: {
  key: FakeDoorKey;
  inviteId: string;
  imageCount?: number;
  bundleId?: string;
}): Promise<SealedBundle> {
  const { key, inviteId, imageCount = 2, bundleId = newBundleId() } = options;
  const images = Array.from({ length: imageCount }, (_unused, index) =>
    new TextEncoder().encode(`${IMAGE_SENTINEL}-photo-${index}`),
  );

  return sealBundle({
    doorPublicKey: key.publication.public_key,
    doorKeyId: key.publication.door_key_id,
    inviteId,
    bundleId,
    manifest: {
      invite_secret: "dGVzdC1pbnZpdGUtc2VjcmV0LXZhbHVl",
      display_name: NAME_SENTINEL,
      consent_version: "v2",
      consent_confirmed: true,
      profile: { profile_id: "sunrise", color: "#ffb300", sound: null },
      captured_at: new Date().toISOString(),
      image_count: images.length,
    },
    images,
  });
}

/** Next.js route handlers receive params as a promise. */
export function routeContext<T extends Record<string, string>>(params: T): { params: Promise<T> } {
  return { params: Promise.resolve(params) };
}
