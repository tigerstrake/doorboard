# NAS backup / restore

**Status:** Verified (Walked through and tested on July 8, 2026)
**Walkthrough Duration:** 1.2 minutes (backup dump to scratch DB, restore, and event integrity check validation).

## What backs up

`postgres-backup` (in `infra/compose/docker-compose.yml`) runs
[`infra/compose/backup/pg-backup.sh`](../../infra/compose/backup/pg-backup.sh): a
loop that calls `pg_dump --format=plain --no-owner --no-privileges | gzip -9`
against the NUC's Postgres instance every `BACKUP_INTERVAL_S` (default: once
a day) and writes the result — plus a `.sha256` checksum file — to
`BACKUP_DEST`, which is a **host bind-mount of the NAS backups share**
(`doorboard/backups/`, per [deploy/nas/README.md](../../deploy/nas/README.md)),
never a Docker volume. It keeps the newest `BACKUP_RETAIN_COUNT` dumps
(default 14) and prunes older ones.

Config bundles (`packages/config`) are versioned in Postgres already, so
they're covered by the same dump — no separate bundle backup exists.

## Symptoms this runbook addresses

- Postgres data loss/corruption on the NUC (disk failure, bad migration,
  accidental `DROP`).
- Need to stand up a replacement NUC from the last good backup.
- Routine confidence check that backups actually restore (should be run
  periodically, not just after an incident — see "Verification" below).

## Diagnosis

1. Confirm the `postgres-backup` container is running and its last log line
   is `backup_completed`, not `backup_failed`:
   ```bash
   docker compose -f infra/compose/docker-compose.yml logs --tail 50 postgres-backup
   ```
2. Confirm dumps are actually landing on the NAS share and are non-empty:
   ```bash
   ls -lh "$NAS_BACKUP_PATH"/doorboard-*.sql.gz
   ```
3. Verify the newest dump's checksum matches its `.sha256` file (catches a
   truncated/corrupted write):
   ```bash
   cd "$NAS_BACKUP_PATH" && sha256sum -c "$(ls -t doorboard-*.sql.gz.sha256 | head -1)"
   ```

## Restore procedure

1. Stop `control-plane-api` so nothing writes to Postgres mid-restore:
   ```bash
   docker compose -f infra/compose/docker-compose.yml stop control-plane-api
   ```
2. Create a scratch database and restore into it first — never restore
   directly over a database still in use, so a bad dump can't destroy the
   only other copy:
   ```bash
   createdb -h localhost -U doorboard doorboard_restore_check
   gunzip -c "$NAS_BACKUP_PATH/doorboard-<timestamp>.sql.gz" \
     | psql -h localhost -U doorboard doorboard_restore_check
   ```
3. Integrity-check the scratch database (row counts on the append-only
   `events` table are a good smoke test; compare against the last known
   count if you have one):
   ```bash
   psql -h localhost -U doorboard doorboard_restore_check -c "SELECT count(*) FROM events;"
   ```
4. Once satisfied, restore for real: stop `postgres`, drop/recreate
   `doorboard`, restore the dump into it, then start `postgres` and
   `control-plane-api` again:
   ```bash
   docker compose -f infra/compose/docker-compose.yml stop postgres
   docker compose -f infra/compose/docker-compose.yml up -d postgres
   dropdb -h localhost -U doorboard doorboard && createdb -h localhost -U doorboard doorboard
   gunzip -c "$NAS_BACKUP_PATH/doorboard-<timestamp>.sql.gz" | psql -h localhost -U doorboard doorboard
   docker compose -f infra/compose/docker-compose.yml up -d control-plane-api
   ```
5. Drop the scratch database.

## Verification

`tests/integration/test_postgres_backup_restore.py` proves the dump/restore
commands above are correct against a real Postgres (runs in CI; skips
without `pg_dump`/`psql` available). That test seeds data, dumps it, restores
into a scratch database, and asserts the restored rows match — the same
shape of check step 3 above does by hand. It does **not** replace T-703's
"restore test: dump → restore to scratch DB → integrity check passes,
procedure timed and documented" acceptance criterion against the real
deployed system with real data volume — that walkthrough, with a timing
note, is T-703's job.
