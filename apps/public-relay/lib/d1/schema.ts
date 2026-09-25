/**
 * The relay's D1 schema (ADR-0043 §1), as an ordered list of migrations.
 *
 * `D1RelayStore.migrate()` applies them in order for the tests. Production applies the
 * matching files under `migrations/` with `wrangler d1 execute` (see the deploy runbook) —
 * nothing runs them lazily on a request. The `migrations/*.sql` files mirror these constants
 * one to one, and `tests/d1Store.test.ts` fails if the schema they build ever differs from
 * the schema these build. If you edit one, edit both.
 *
 * Every row that expires carries an `*_expires_ms` epoch-millis column. Reads filter on it,
 * so a stale row is never *served*; the sweep (`sweepExpired`, driven from the Pi's pickup
 * polls via `meta.last_sweep_ms`) is what stops it being *held* — that is what preserves the
 * retention windows of ADR-0016 §5 on a store without native TTLs.
 *
 * Why D1 (relational) and not KV: the invite `consumed` flag, the pickup lease, the
 * never-regressing terminal status, and the fixed-window rate limiter all need atomic
 * read-modify-write, which KV's eventual consistency cannot give. Each is a single guarded
 * SQL statement here.
 */
const INIT_0001 = `
CREATE TABLE IF NOT EXISTS door_key (
  id           TEXT PRIMARY KEY,          -- always 'active'
  record_json  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invite (
  invite_id      TEXT PRIMARY KEY,
  secret_sha256  TEXT NOT NULL,
  expires_at     TEXT NOT NULL,           -- ISO string, echoed back to the phone
  max_images     INTEGER NOT NULL,
  consumed       INTEGER NOT NULL DEFAULT 0,
  expires_ms     INTEGER NOT NULL         -- row TTL: invite expiry + grace
);

CREATE TABLE IF NOT EXISTS bundle (
  bundle_id          TEXT PRIMARY KEY,
  invite_id          TEXT NOT NULL,
  bundle_json        TEXT,                -- sealed ciphertext; NULL after finish/expiry
  submitted_at       TEXT NOT NULL,
  created_ms         INTEGER NOT NULL,    -- FIFO order for pending
  bundle_expires_ms  INTEGER NOT NULL,    -- ciphertext TTL (15 min)
  lease_until_ms     INTEGER NOT NULL DEFAULT 0,
  in_pending         INTEGER NOT NULL DEFAULT 1,
  status             TEXT NOT NULL DEFAULT 'pending',
  status_reason      TEXT,
  status_updated_at  TEXT NOT NULL,
  status_expires_ms  INTEGER NOT NULL     -- status TTL (1 h), outlives the ciphertext
);
CREATE INDEX IF NOT EXISTS idx_bundle_pending ON bundle (in_pending, lease_until_ms, created_ms);

CREATE TABLE IF NOT EXISTS rate_limit (
  key                TEXT PRIMARY KEY,     -- scope:subject
  count              INTEGER NOT NULL,
  window_expires_ms  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS visitor_snapshot (
  session_id     TEXT PRIMARY KEY,
  token_sha256   TEXT NOT NULL,
  snapshot_json  TEXT NOT NULL,
  expires_ms     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visitor_snapshot_token ON visitor_snapshot (token_sha256);

CREATE TABLE IF NOT EXISTS visitor_action (
  action_id       TEXT PRIMARY KEY,
  session_id      TEXT NOT NULL,
  action_json     TEXT NOT NULL,
  created_ms      INTEGER NOT NULL,
  lease_until_ms  INTEGER NOT NULL DEFAULT 0,
  expires_ms      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visitor_action_pending ON visitor_action (lease_until_ms, created_ms);
`;

/** Relay-wide bookkeeping; today only `last_sweep_ms`, the slot the retention sweep claims. */
const META_0002 = `
CREATE TABLE IF NOT EXISTS meta (
  key    TEXT PRIMARY KEY,
  value  INTEGER NOT NULL
);
`;

/** In application order. `migrations/000N_*.sql` on disk is the same list, one file each. */
export const MIGRATIONS: readonly string[] = [INIT_0001, META_0002];
