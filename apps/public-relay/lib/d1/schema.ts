/**
 * The relay's D1 schema (ADR-0043 §1). Single source of truth: the store's `migrate()`
 * runs this, the tests run this, and `migrations/0001_init.sql` (for `wrangler d1 migrations`)
 * is generated from it. Every row that expires carries an `*_expires_ms` epoch-millis column,
 * swept lazily on read and by a scheduled sweep — this replaces Redis native key TTLs while
 * preserving the exact retention windows from ADR-0016 §5.
 *
 * Why D1 (relational) and not KV: the invite `consumed` flag, the pickup lease, the
 * never-regressing terminal status, and the fixed-window rate limiter all need atomic
 * read-modify-write, which KV's eventual consistency cannot give. Each is a single guarded
 * SQL statement here.
 */
export const SCHEMA_SQL = `
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
