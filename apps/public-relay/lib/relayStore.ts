/**
 * Store resolution (ADR-0043 §1). A route obtains its {@link RelayStore} from here and never
 * knows which backend it got:
 *
 *   - `resolveStore(env.DB)` → the Cloudflare D1 store, in production on Pages Functions.
 *   - `resolveStore()`       → the Upstash Redis store, for local `next dev` and the vitest
 *                              suite (which injects a fake via `setRedisForTests`).
 *
 * The store is constructed per call from the request's binding, never held in a module global —
 * a Worker isolate serves many requests, so a shared mutable "current store" would race.
 */
import * as redisStore from "./store";
import { D1RelayStore } from "./d1/d1Store";
import type { D1Like } from "./d1/types";
import type {
  BundleState,
  DoorKeyRecord,
  RelayStore,
  StoredBundle,
  StoredInvite,
  StoredStatus,
  StoredVisitorSnapshot,
} from "./relayTypes";
import type { SealedBundle } from "./contracts";

/** Adapts the Redis free-function store to the {@link RelayStore} interface by delegation. */
class RedisRelayStore implements RelayStore {
  configured(): boolean {
    return redisStore.storageConfigured();
  }
  putDoorKey(record: DoorKeyRecord): Promise<void> {
    return redisStore.putDoorKey(record);
  }
  getDoorKey(): Promise<DoorKeyRecord | null> {
    return redisStore.getDoorKey();
  }
  putInvite(inviteId: string, invite: Omit<StoredInvite, "consumed">): Promise<void> {
    return redisStore.putInvite(inviteId, invite);
  }
  getInvite(inviteId: string): Promise<StoredInvite | null> {
    return redisStore.getInvite(inviteId);
  }
  markInviteConsumed(inviteId: string): Promise<void> {
    return redisStore.markInviteConsumed(inviteId);
  }
  deleteInvite(inviteId: string): Promise<void> {
    return redisStore.deleteInvite(inviteId);
  }
  putBundle(bundle: SealedBundle): Promise<string> {
    return redisStore.putBundle(bundle);
  }
  leaseBundles(): Promise<StoredBundle[]> {
    return redisStore.leaseBundles();
  }
  finishBundle(
    bundleId: string,
    status: BundleState,
    reason: string | null,
  ): Promise<string | null> {
    return redisStore.finishBundle(bundleId, status, reason);
  }
  setStatus(bundleId: string, status: BundleState, reason: string | null): Promise<void> {
    return redisStore.setStatus(bundleId, status, reason);
  }
  getStatus(bundleId: string): Promise<StoredStatus | null> {
    return redisStore.getStatus(bundleId);
  }
  pendingCount(): Promise<number> {
    return redisStore.pendingCount();
  }
  putVisitorSnapshot(snapshot: StoredVisitorSnapshot): Promise<void> {
    return redisStore.putVisitorSnapshot(snapshot);
  }
  getVisitorSnapshotByTokenHash(tokenHash: string): Promise<StoredVisitorSnapshot | null> {
    return redisStore.getVisitorSnapshotByTokenHash(tokenHash);
  }
  queueVisitorAction(actionId: string, action: unknown): Promise<void> {
    return redisStore.queueVisitorAction(actionId, action);
  }
  leaseVisitorActions(): Promise<unknown[]> {
    return redisStore.leaseVisitorActions();
  }
  completeVisitorAction(sessionId: string, actionId: string, outcome: unknown): Promise<void> {
    return redisStore.completeVisitorAction(sessionId, actionId, outcome);
  }
  pendingVisitorActionCount(): Promise<number> {
    return redisStore.pendingVisitorActionCount();
  }
  underRateLimit(
    scope: string,
    subject: string,
    limit: number,
    windowS: number,
  ): Promise<boolean> {
    return redisStore.underRateLimit(scope, subject, limit, windowS);
  }
}

const redisSingleton = new RedisRelayStore();

/** Resolve the store for one request: D1 when a binding is present, else Redis. */
export function resolveStore(db?: D1Like | null): RelayStore {
  return db ? new D1RelayStore(db) : redisSingleton;
}

export type { RelayStore };
