/**
 * The subset of the Cloudflare D1 API the relay store uses (ADR-0043 §1).
 *
 * Declared structurally rather than importing `@cloudflare/workers-types` so the store
 * and its tests share one contract, and a `node:sqlite`-backed double (tests) and the real
 * `env.DB` binding (production) both satisfy it. Every method is async: D1 returns Promises,
 * and the test adapter wraps its synchronous SQLite calls to match.
 */

export interface D1Result<T = Record<string, unknown>> {
  results: T[];
  meta: { changes: number };
}

export interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<D1Result<T>>;
  run(): Promise<D1Result>;
}

export interface D1Like {
  prepare(query: string): D1PreparedStatement;
  exec(query: string): Promise<unknown>;
}
