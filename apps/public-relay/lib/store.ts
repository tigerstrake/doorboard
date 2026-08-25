/**
 * Relay storage (ADR-0016 §5). Upstash Redis, TTL on every key.
 *
 * Everything here is either ciphertext or an opaque identifier. There is no key,
 * field, or code path that stores a face image, a display name, or an embedding —
 * those are sealed inside `bundle.items` and unreadable to this process (E-9).
 *
 * A bundle is leased rather than deleted on pickup: if the Pi dies between
 * collecting and acking, the lease lapses and the bundle becomes collectable
 * again. Single-use invites (E-11) make a duplicate delivery harmless.
 */
import { Redis } from "@upstash/redis";

import type { SealedBundle } from "./contracts";

export const BUNDLE_TTL_S = 15 * 60;
export const STATUS_TTL_S = 60 * 60;
export const INVITE_GRACE_S = 60 * 60;
export const PICKUP_LEASE_S = 60;
export const MAX_PICKUP_BATCH = 8;

export type BundleState = "pending" | "collected" | "enrolled" | "failed" | "expired";

/** Terminal states never regress — a late duplicate ack cannot un-enroll someone. */
const TERMINAL: ReadonlySet<BundleState> = new Set<BundleState>(["enrolled", "expired"]);

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

const KEY = {
  invite: (id: string) => `invite:${id}`,
  bundle: (id: string) => `bundle:${id}`,
  status: (id: string) => `status:${id}`,
  pending: "pending",
  doorKey: "doorkey:active",
  rate: (scope: string, subject: string) => `rl:${scope}:${subject}`,
} as const;

/**
 * Exactly the operations this module uses, declared structurally rather than as
 * `Pick<Redis, …>`: the real client's signatures are generic in ways a test double
 * cannot satisfy, and narrowing here keeps the storage surface visible at a glance.
 */
interface RedisLike {
  set(key: string, value: string, options?: { ex?: number }): Promise<unknown>;
  get(key: string): Promise<unknown>;
  del(key: string): Promise<unknown>;
  incr(key: string): Promise<number>;
  expire(key: string, seconds: number): Promise<unknown>;
  zadd(key: string, member: { score: number; member: string }): Promise<unknown>;
  // Only the by-score form is used, and the real client requires `options` for
  // it, so this is declared non-optional to stay assignable from `Redis`.
  zrange(
    key: string,
    min: number,
    max: number,
    options: { byScore: true; offset: number; count: number },
  ): Promise<unknown[]>;
  zrem(key: string, member: string): Promise<unknown>;
  zcard(key: string): Promise<number>;
}

let client: RedisLike | null = null;
let testClient: RedisLike | null = null;

/**
 * Substitute the storage client. **Tests only** — production always resolves the
 * real client from the environment.
 */
export function setRedisForTests(fake: RedisLike | null): void {
  testClient = fake;
  client = null;
}

/**
 * Resolve the Redis client from either naming scheme: the Vercel marketplace
 * integration injects `KV_REST_API_*`, a manual Upstash setup uses
 * `UPSTASH_REDIS_REST_*`.
 */
export function redis(): RedisLike {
  if (testClient) return testClient;
  if (client) return client;
  const url = process.env.UPSTASH_REDIS_REST_URL ?? process.env.KV_REST_API_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN ?? process.env.KV_REST_API_TOKEN;
  if (!url || !token) {
    throw new Error(
      "relay storage is not configured: set UPSTASH_REDIS_REST_URL and " +
        "UPSTASH_REDIS_REST_TOKEN (or the KV_REST_API_* pair)",
    );
  }
  const resolved: RedisLike = new Redis({ url, token });
  client = resolved;
  return resolved;
}

export function storageConfigured(): boolean {
  return Boolean(
    (process.env.UPSTASH_REDIS_REST_URL ?? process.env.KV_REST_API_URL) &&
      (process.env.UPSTASH_REDIS_REST_TOKEN ?? process.env.KV_REST_API_TOKEN),
  );
}

/** Seconds a record should live for, given a wall-clock expiry, floored at 1. */
function ttlUntil(expiresAt: string, graceS: number): number {
  const remaining = Math.floor((Date.parse(expiresAt) - Date.now()) / 1000);
  return Math.max(1, remaining + graceS);
}

// -- door key ---------------------------------------------------------------

export async function putDoorKey(record: DoorKeyRecord): Promise<void> {
  await redis().set(KEY.doorKey, JSON.stringify(record));
}

export async function getDoorKey(): Promise<DoorKeyRecord | null> {
  return readJson<DoorKeyRecord>(await redis().get(KEY.doorKey));
}

// -- invites ----------------------------------------------------------------

export async function putInvite(
  inviteId: string,
  invite: Omit<StoredInvite, "consumed">,
): Promise<void> {
  const existing = await getInvite(inviteId);
  // Re-registration (the Pi resyncs after an outage) must not resurrect an
  // invite we already saw consumed.
  const consumed = existing?.consumed ?? false;
  await redis().set(KEY.invite(inviteId), JSON.stringify({ ...invite, consumed }), {
    ex: ttlUntil(invite.expires_at, INVITE_GRACE_S),
  });
}

export async function getInvite(inviteId: string): Promise<StoredInvite | null> {
  return readJson<StoredInvite>(await redis().get(KEY.invite(inviteId)));
}

export async function markInviteConsumed(inviteId: string): Promise<void> {
  const invite = await getInvite(inviteId);
  if (!invite) return;
  await redis().set(KEY.invite(inviteId), JSON.stringify({ ...invite, consumed: true }), {
    ex: ttlUntil(invite.expires_at, INVITE_GRACE_S),
  });
}

export async function deleteInvite(inviteId: string): Promise<void> {
  await redis().del(KEY.invite(inviteId));
}

// -- bundles ----------------------------------------------------------------

export async function putBundle(bundle: SealedBundle): Promise<string> {
  const submittedAt = new Date().toISOString();
  const record: StoredBundle = { bundle, submitted_at: submittedAt };
  const r = redis();
  await r.set(KEY.bundle(bundle.bundle_id), JSON.stringify(record), { ex: BUNDLE_TTL_S });
  // Score is "collectable from this instant" — see leaseBundles.
  await r.zadd(KEY.pending, { score: Date.now(), member: bundle.bundle_id });
  await setStatus(bundle.bundle_id, "pending", null);
  return submittedAt;
}

/**
 * Claim up to {@link MAX_PICKUP_BATCH} collectable bundles and push their next
 * collectable time out by the lease, so a concurrent poll does not take the same
 * work.
 */
export async function leaseBundles(): Promise<StoredBundle[]> {
  const r = redis();
  const now = Date.now();
  const ids = (await r.zrange(KEY.pending, 0, now, {
    byScore: true,
    offset: 0,
    count: MAX_PICKUP_BATCH,
  })) as string[];
  if (ids.length === 0) return [];

  const leased: StoredBundle[] = [];
  for (const id of ids) {
    const record = readJson<StoredBundle>(await r.get(KEY.bundle(id)));
    if (!record) {
      // The bundle's own TTL lapsed; drop the dangling queue entry.
      await r.zrem(KEY.pending, id);
      await setStatus(id, "expired", "bundle_expired");
      continue;
    }
    await r.zadd(KEY.pending, { score: now + PICKUP_LEASE_S * 1000, member: id });
    await setStatus(id, "collected", null);
    leased.push(record);
  }
  return leased;
}

/**
 * Delete the ciphertext and record the outcome (ADR-0016 §5).
 *
 * Returns the bundle's `invite_id` when it was still present, so the caller can
 * close the invite out without the Pi having to tell us which one it was.
 */
export async function finishBundle(
  bundleId: string,
  status: BundleState,
  reason: string | null,
): Promise<string | null> {
  const r = redis();
  const record = readJson<StoredBundle>(await r.get(KEY.bundle(bundleId)));
  await r.del(KEY.bundle(bundleId));
  await r.zrem(KEY.pending, bundleId);
  await setStatus(bundleId, status, reason);
  return record?.bundle.invite_id ?? null;
}

export async function setStatus(
  bundleId: string,
  status: BundleState,
  reason: string | null,
): Promise<void> {
  const current = await getStatus(bundleId);
  if (current && TERMINAL.has(current.status) && current.status !== status) return;
  const record: StoredStatus = { status, reason, updated_at: new Date().toISOString() };
  await redis().set(KEY.status(bundleId), JSON.stringify(record), { ex: STATUS_TTL_S });
}

export async function getStatus(bundleId: string): Promise<StoredStatus | null> {
  return readJson<StoredStatus>(await redis().get(KEY.status(bundleId)));
}

export async function pendingCount(): Promise<number> {
  return redis().zcard(KEY.pending);
}

// -- visitor surface (ADR-0017) --------------------------------------------

/**
 * A visitor session lives as long as the door session plus a small grace. Short
 * on purpose: this is transient interaction state, and nothing here should outlive
 * the person standing at the door by more than a few minutes.
 */
export const VISITOR_SNAPSHOT_GRACE_S = 5 * 60;
export const VISITOR_ACTION_TTL_S = 15 * 60;
export const VISITOR_LEASE_S = 30;
export const MAX_VISITOR_BATCH = 16;

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

const VISITOR_KEY = {
  snapshot: (sessionId: string) => `visitor:snap:${sessionId}`,
  byToken: (tokenHash: string) => `visitor:tok:${tokenHash}`,
  action: (actionId: string) => `visitor:act:${actionId}`,
  pendingActions: "visitor:pending",
} as const;

export async function putVisitorSnapshot(snapshot: StoredVisitorSnapshot): Promise<void> {
  const ttl = ttlUntil(snapshot.expires_at, VISITOR_SNAPSHOT_GRACE_S);
  const r = redis();
  await r.set(VISITOR_KEY.snapshot(snapshot.session_id), JSON.stringify(snapshot), { ex: ttl });
  // A phone presents a token, not a session id, so keep a hash → session lookup.
  await r.set(VISITOR_KEY.byToken(snapshot.session_token_sha256), snapshot.session_id, { ex: ttl });
}

export async function getVisitorSnapshotByTokenHash(
  tokenHash: string,
): Promise<StoredVisitorSnapshot | null> {
  const sessionId = await redis().get(VISITOR_KEY.byToken(tokenHash));
  if (typeof sessionId !== "string" || sessionId.length === 0) return null;
  const snapshot = readJson<StoredVisitorSnapshot>(
    await redis().get(VISITOR_KEY.snapshot(sessionId)),
  );
  if (!snapshot) return null;
  // Defence in depth: the hash must still match the snapshot we found, so a stale
  // pointer cannot hand one visitor another visitor's session.
  return snapshot.session_token_sha256 === tokenHash ? snapshot : null;
}

export async function queueVisitorAction(actionId: string, action: unknown): Promise<void> {
  const r = redis();
  await r.set(VISITOR_KEY.action(actionId), JSON.stringify(action), { ex: VISITOR_ACTION_TTL_S });
  await r.zadd(VISITOR_KEY.pendingActions, { score: Date.now(), member: actionId });
}

/** Claim pending visitor actions, leasing them so a concurrent poll skips them. */
export async function leaseVisitorActions(): Promise<unknown[]> {
  const r = redis();
  const now = Date.now();
  const ids = (await r.zrange(VISITOR_KEY.pendingActions, 0, now, {
    byScore: true,
    offset: 0,
    count: MAX_VISITOR_BATCH,
  })) as string[];

  const leased: unknown[] = [];
  for (const id of ids) {
    const action = readJson<unknown>(await r.get(VISITOR_KEY.action(id)));
    if (!action) {
      await r.zrem(VISITOR_KEY.pendingActions, id);
      continue;
    }
    await r.zadd(VISITOR_KEY.pendingActions, { score: now + VISITOR_LEASE_S * 1000, member: id });
    leased.push(action);
  }
  return leased;
}

/** Retire a collected action and fold its outcome into the visitor's snapshot. */
export async function completeVisitorAction(
  sessionId: string,
  actionId: string,
  outcome: unknown,
): Promise<void> {
  const r = redis();
  await r.del(VISITOR_KEY.action(actionId));
  await r.zrem(VISITOR_KEY.pendingActions, actionId);

  const snapshot = readJson<StoredVisitorSnapshot>(await r.get(VISITOR_KEY.snapshot(sessionId)));
  if (!snapshot) return;
  const outcomes = [
    ...snapshot.outcomes.filter(
      (existing) => (existing as { action_id?: string }).action_id !== actionId,
    ),
    outcome,
  ].slice(-16);
  await r.set(
    VISITOR_KEY.snapshot(sessionId),
    JSON.stringify({ ...snapshot, outcomes }),
    { ex: ttlUntil(snapshot.expires_at, VISITOR_SNAPSHOT_GRACE_S) },
  );
}

export async function pendingVisitorActionCount(): Promise<number> {
  return redis().zcard(VISITOR_KEY.pendingActions);
}

// -- rate limiting ----------------------------------------------------------

/** Fixed-window counter. Returns true when the caller is still within budget. */
export async function underRateLimit(
  scope: string,
  subject: string,
  limit: number,
  windowS: number,
): Promise<boolean> {
  const key = KEY.rate(scope, subject);
  const r = redis();
  const count = await r.incr(key);
  if (count === 1) await r.expire(key, windowS);
  return count <= limit;
}

/**
 * Upstash returns already-parsed objects when a value is JSON, and a string
 * otherwise. Tolerate both rather than assuming one.
 */
function readJson<T>(raw: unknown): T | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw) as T;
    } catch {
      return null;
    }
  }
  return raw as T;
}
