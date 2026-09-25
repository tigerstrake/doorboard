/**
 * Store resolution (ADR-0043 §1). A route obtains its {@link RelayStore} from here and never
 * touches the binding itself: `resolveStore(env.DB)` → the Cloudflare D1 store. The vitest
 * suite passes a `node:sqlite`-backed double through the same call, so the tests drive the
 * store production uses rather than a double for a backend it no longer has.
 *
 * The store is constructed per call from the request's binding, never held in a module global —
 * a Worker isolate serves many requests, so a shared mutable "current store" would race.
 */
import { D1RelayStore } from "./d1/d1Store";
import type { D1Like } from "./d1/types";
import type { RelayStore } from "./relayTypes";

/**
 * Resolve the store for one request. A missing binding (a misconfigured deployment) yields a
 * store whose `configured()` is false, which every route answers with `503
 * storage_not_configured` and `/api/health` reports — a signal, not a TypeError.
 */
export function resolveStore(db: D1Like | null | undefined): RelayStore {
  return new D1RelayStore(db ?? null);
}

export type { RelayStore };
