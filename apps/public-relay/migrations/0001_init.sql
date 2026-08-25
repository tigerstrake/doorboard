-- Relay D1 schema (ADR-0043 §1). Apply with:
--   wrangler d1 execute doorboard-relay --file=./migrations/0001_init.sql --remote
-- This mirrors lib/d1/schema.ts (SCHEMA_SQL), which is the single source of truth: the Worker
-- also runs it lazily and idempotently on first request, so applying this by hand is optional.
-- If you edit one, edit both.

CREATE TABLE IF NOT EXISTS door_key (
  id           TEXT PRIMARY KEY,
  record_json  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invite (
  invite_id      TEXT PRIMARY KEY,
  secret_sha256  TEXT NOT NULL,
  expires_at     TEXT NOT NULL,
  max_images     INTEGER NOT NULL,
  consumed       INTEGER NOT NULL DEFAULT 0,
  expires_ms     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bundle (
  bundle_id          TEXT PRIMARY KEY,
  invite_id          TEXT NOT NULL,
  bundle_json        TEXT,
  submitted_at       TEXT NOT NULL,
  created_ms         INTEGER NOT NULL,
  bundle_expires_ms  INTEGER NOT NULL,
  lease_until_ms     INTEGER NOT NULL DEFAULT 0,
  in_pending         INTEGER NOT NULL DEFAULT 1,
  status             TEXT NOT NULL DEFAULT 'pending',
  status_reason      TEXT,
  status_updated_at  TEXT NOT NULL,
  status_expires_ms  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bundle_pending ON bundle (in_pending, lease_until_ms, created_ms);

CREATE TABLE IF NOT EXISTS rate_limit (
  key                TEXT PRIMARY KEY,
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
