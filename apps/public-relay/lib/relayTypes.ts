/**
 * Storage-layer types, retention constants, and the `RelayStore` interface — the contract
 * shared by the two backends (Upstash Redis in `store.ts`, Cloudflare D1 in `d1/d1Store.ts`).
 *
 * Dependency-free on purpose: importing a backend must not drag in the other's client. The
 * D1 store imports its constants and types from here, so nothing on the Cloudflare path pulls
 * in `@upstash/redis`.
 *
 * Everything a store holds is ciphertext or an opaque identifier (E-9); the visitor snapshot
 * carries a boolean `attributed`, never a name (ADR-0044).
 */
import type { SealedBundle } from "./contracts";

export const BUNDLE_TTL_S = 15 * 60;
export const STATUS_TTL_S = 60 * 60;
export const INVITE_GRACE_S = 60 * 60;
export const PICKUP_LEASE_S = 60;
export const MAX_PICKUP_BATCH = 8;

export const VISITOR_SNAPSHOT_GRACE_S = 5 * 60;
export const VISITOR_ACTION_TTL_S = 15 * 60;
export const VISITOR_LEASE_S = 30;
export const MAX_VISITOR_BATCH = 16;

export type BundleState = "pending" | "collected" | "enrolled" | "failed" | "expired";

/** Terminal states never regress — a late duplicate ack cannot un-enroll someone. */
export const TERMINAL: ReadonlySet<BundleState> = new Set<BundleState>(["enrolled", "expired"]);

export interface StoredInvite {
  secret_sha256: string;
  expires_at: string;
  max_images: number;
  consumed: boolean;
}

export interface StoredBundle {
  bundle: SealedBundle;
  submitted_at: string;
}

export interface StoredStatus {
  status: BundleState;
  reason: string | null;
  updated_at: string;
}

export interface DoorKeyRecord {
  door_key_id: string;
  suite: string;
  public_key: string;
  fingerprint: string;
  consent_version: string;
  consent_text: string;
  published_at: string;
}

export interface StoredVisitorSnapshot {
  session_token_sha256: string;
  session_id: string;
  state: string;
  expires_at: string;
  poll: unknown;
  poll_results: unknown;
  outcomes: unknown[];
  attributed: boolean;
  pushed_at: string;
}

/**
 * The storage contract both backends satisfy. `configured()` reports whether the backend has
 * what it needs to serve (a D1 binding, or the Redis env vars); a route returns 503 when not.
 */
export interface RelayStore {
  configured(): boolean;

  putDoorKey(record: DoorKeyRecord): Promise<void>;
  getDoorKey(): Promise<DoorKeyRecord | null>;

  putInvite(inviteId: string, invite: Omit<StoredInvite, "consumed">): Promise<void>;
  getInvite(inviteId: string): Promise<StoredInvite | null>;
  markInviteConsumed(inviteId: string): Promise<void>;
  deleteInvite(inviteId: string): Promise<void>;

  putBundle(bundle: SealedBundle): Promise<string>;
  leaseBundles(): Promise<StoredBundle[]>;
  finishBundle(
    bundleId: string,
    status: BundleState,
    reason: string | null,
  ): Promise<string | null>;
  setStatus(bundleId: string, status: BundleState, reason: string | null): Promise<void>;
  getStatus(bundleId: string): Promise<StoredStatus | null>;
  pendingCount(): Promise<number>;

  putVisitorSnapshot(snapshot: StoredVisitorSnapshot): Promise<void>;
  getVisitorSnapshotByTokenHash(tokenHash: string): Promise<StoredVisitorSnapshot | null>;
  queueVisitorAction(actionId: string, action: unknown): Promise<void>;
  leaseVisitorActions(): Promise<unknown[]>;
  completeVisitorAction(sessionId: string, actionId: string, outcome: unknown): Promise<void>;
  pendingVisitorActionCount(): Promise<number>;

  underRateLimit(
    scope: string,
    subject: string,
    limit: number,
    windowS: number,
  ): Promise<boolean>;
}
