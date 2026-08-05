/**
 * Relay wire shapes, re-exported from `packages/contracts` (ADR-0016 E-13).
 *
 * These are **type-only** re-exports: the generated interfaces are the single
 * source of truth shared with the Pi's Pydantic models, and importing them as
 * types keeps the build free of any runtime dependency on the workspace package.
 * Runtime checking is `lib/validate.ts`, whose tests pin it to these shapes.
 */
export type {
  BundleStatus,
  BundleSubmitAccepted,
  DoorKeyPublication,
  InvitePublicState,
  InviteRegistration,
  PickupAck,
  PickupBatch,
  PickupItem,
  RelayHealth,
  SealedBundle,
  SealedItem,
  SealedManifest,
  SealedProfile,
} from "@doorboard/contracts";

/** The one permitted seal suite. Mirrors `SEAL_SUITE` in the Python contracts. */
export const SEAL_SUITE = "ecies-p256-hkdf-sha256-aes256gcm" as const;

/** HKDF `info` prefix. Mirrors `SEAL_INFO_PREFIX`. */
export const SEAL_INFO_PREFIX = "doorboard/enroll-relay/v1" as const;
