#!/bin/sh
# Scheduled `pg_dump` loop for the NUC Postgres instance (T-503).
#
# Runs inside the `postgres-backup` compose service (image: postgres:16,
# which ships pg_dump/pg_restore matching the server version — ADR-0007:
# dumps go *to* the NAS, the NUC never becomes the archive). This is a
# `while true; do dump; sleep` loop rather than cron because it needs no
# extra packages in the image, and the failure mode of "sleep loop dies" is
# exactly as visible to `docker compose ps`/healthcheck as any other crashed
# process — see docs/runbooks/nas-backup-restore.md for the restore side.
set -eu

: "${BACKUP_INTERVAL_S:=86400}"       # default: once a day
: "${BACKUP_RETAIN_COUNT:=14}"        # keep the last N dumps on the NAS path
: "${BACKUP_DEST:?BACKUP_DEST (NAS-mounted path) must be set}"
: "${POSTGRES_DSN:?POSTGRES_DSN must be set}"
: "${BACKUP_MARKER:=.doorboard-nas}"    # must exist ON the share, never created here

log() {
  # Structured JSON, per CONTRIBUTING.md's logging convention.
  printf '{"time":"%s","level":"%s","service":"postgres-backup","message":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2"
}

# ADR-0007: dumps go *to* the NAS; the NUC never becomes the archive. BACKUP_DEST
# is a host bind mount, and a host path whose mount is absent is indistinguishable
# from an ordinary empty directory — so a NAS that is unreachable at boot would
# silently redirect every dump onto the NUC's own disk, the one disk a backup
# exists to survive. It failed exactly that way until 2026-08-21: a duplicate
# NAS_BACKUP_PATH in the NUC's .env won (compose --env-file keeps the last
# occurrence) and pointed at local disk. Require a marker that only lives on the
# share, and never create it here — creating it is what would defeat the check.
require_nas_mounted() {
  if [ ! -e "$BACKUP_DEST/$BACKUP_MARKER" ]; then
    log error "backup_dest_not_mounted dest=$BACKUP_DEST marker=$BACKUP_MARKER"
    return 1
  fi
  return 0
}

run_backup() {
  mkdir -p "$BACKUP_DEST"
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  dump_path="$BACKUP_DEST/doorboard-$ts.sql.gz"
  tmp_path="$dump_path.partial"

  log info "backup_starting path=$dump_path"
  if pg_dump --dbname="$POSTGRES_DSN" --format=plain --no-owner --no-privileges \
      | gzip -9 > "$tmp_path"; then
    mv "$tmp_path" "$dump_path"
    sha256sum "$dump_path" > "$dump_path.sha256"
    size=$(wc -c < "$dump_path")
    log info "backup_completed path=$dump_path size_bytes=$size"
  else
    rm -f "$tmp_path"
    log error "backup_failed path=$dump_path"
    return 1
  fi

  # Prune: keep only the newest BACKUP_RETAIN_COUNT dumps (+ their checksums).
  # shellcheck disable=SC2012
  ls -1t "$BACKUP_DEST"/doorboard-*.sql.gz 2>/dev/null \
    | tail -n "+$((BACKUP_RETAIN_COUNT + 1))" \
    | while IFS= read -r stale; do
        log info "backup_pruned path=$stale"
        rm -f "$stale" "$stale.sha256"
      done
}

log info "postgres-backup loop starting interval_s=$BACKUP_INTERVAL_S dest=$BACKUP_DEST"
while true; do
  # Retry rather than exit: an unreachable NAS is transient (and expected while
  # the door moves networks), so the loop keeps logging until the share is back.
  if require_nas_mounted; then
    run_backup || log error "backup_iteration_failed"
  fi
  sleep "$BACKUP_INTERVAL_S"
done
