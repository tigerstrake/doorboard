-- Relay D1 migration 0002: the `meta` table the retention sweep claims its slot in. Apply with:
--   wrangler d1 execute doorboard-relay --remote --file=./migrations/0002_meta.sql
-- Mirrors META_0002 in lib/d1/schema.ts; if you edit one, edit both. Nothing applies this
-- lazily: a deployment whose code expects it fails its pickup polls until it is applied, on
-- purpose — the sweep is a privacy control (ADR-0016 §5 retention), not housekeeping.

CREATE TABLE IF NOT EXISTS meta (
  key    TEXT PRIMARY KEY,
  value  INTEGER NOT NULL
);
