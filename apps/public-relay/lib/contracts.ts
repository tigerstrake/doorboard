/**
 * Relay wire shapes (ADR-0016 E-13).
 *
 * These mirror `packages/contracts/types/index.ts`, which is generated from the
 * Pydantic models the door Pi validates against — that file is the source of
 * truth for the format.
 *
 * They are **redeclared here rather than imported** because this app is the one
 * artifact deployed outside the house, and it has to build from its own directory
 * alone: a `workspace:*` dependency cannot resolve when Vercel uploads only this
 * subtree. Fidelity is not left to good intentions —
 * `tests/contractParity.test.ts` reads the generated file off disk and fails if
 * any field here drifts from it, so CI catches divergence even though the build
 * does not link the package.
 *
 * If you change a shape: edit
 * `packages/contracts/src/doorboard_contracts/enrollment_relay.py`, regenerate
 * (`contracts generate-ts`), then update this file until the parity test passes.
 */

/** Base64url, unpadded. Every opaque blob and identifier crossing the relay. */
export type Base64Url = string;

export interface SealedProfile {
  profile_id: string;
  color: string;
  sound?: string | null;
}

/**
 * Item 0 of a sealed bundle: plaintext, and never a relay request field.
 *
 * This is the one shape carrying nominal data, which is exactly why it lives
 * inside the AEAD envelope (ADR-0016 §1).
 */
export interface SealedManifest {
  invite_secret: Base64Url;
  display_name: string;
  consent_version: string;
  consent_confirmed: true;
  profile: SealedProfile;
  captured_at: string;
  image_count: number;
}

export interface SealedItem {
  index: number;
  nonce: Base64Url;
  ciphertext: Base64Url;
}

export interface SealedBundle {
  v: 1;
  suite: "ecies-p256-hkdf-sha256-aes256gcm";
  bundle_id: string;
  invite_id: string;
  door_key_id: string;
  ephemeral_public_key: Base64Url;
  salt: Base64Url;
  items: Array<SealedItem>;
}

export interface DoorKeyPublication {
  door_key_id: string;
  suite: "ecies-p256-hkdf-sha256-aes256gcm";
  public_key: Base64Url;
  fingerprint: Base64Url;
  consent_version: string;
  consent_text: string;
  published_at: string;
}

export interface InviteRegistration {
  invite_id: string;
  secret_sha256: Base64Url;
  expires_at: string;
  max_images: number;
}

export interface InvitePublicState {
  invite_id: string;
  status: "open" | "consumed" | "expired" | "revoked" | "unknown";
  max_images: number;
  expires_at?: string | null;
}

export interface BundleSubmitAccepted {
  bundle_id: string;
  status: "pending";
  expires_at: string;
}

export interface BundleStatus {
  bundle_id: string;
  status: "pending" | "collected" | "enrolled" | "failed" | "expired";
  reason?: string | null;
  updated_at: string;
}

export interface PickupItem {
  bundle: SealedBundle;
  submitted_at: string;
}

export interface PickupBatch {
  items: Array<PickupItem>;
}

export interface PickupAck {
  bundle_id: string;
  outcome: "enrolled" | "failed" | "rejected";
  reason?: string | null;
}

export interface RelayHealth {
  status: "ok" | "degraded";
  pending_bundles: number;
}

/** The one permitted seal suite. Mirrors `SEAL_SUITE` in the Python contracts. */
export const SEAL_SUITE = "ecies-p256-hkdf-sha256-aes256gcm" as const;

/** HKDF `info` prefix. Mirrors `SEAL_INFO_PREFIX`. */
export const SEAL_INFO_PREFIX = "doorboard/enroll-relay/v1" as const;
