# deploy/nas — archive configuration

The NAS is a durable archive, never an active database or recording target (ADR-0007).

- Shares/structure: `doorboard/recordings/` and `doorboard/thumbnails/` (what door-sync uploads — `SYNC_SYNCABLE_ROOTS=recordings,thumbnails`, mounted on the Pi at `/mnt/nas/doorboard`), `doorboard/gallery/`, `doorboard/backups/` (Postgres dumps + `.sha256`, written by the NUC's `postgres-backup`; the share must carry the `.doorboard-nas` marker file or the job refuses to write).
- Access: one limited-scope service account for uploads (used by door-sync); admin credentials never leave the NAS/NUC.
- Backup verification: the restore procedure is `docs/runbooks/nas-backup-restore.md` (manual — nothing schedules it). The automated check is the backup-stale healthcheck on `postgres-backup` (`infra/monitoring/README.md`). An unverified backup is not a backup: walk the restore after any Postgres change.
- Retention policy for archived clips: **still to be defined with the owner** (audit backlog §D). Indefinite-by-default violates the handoff's "must not retain all raw media forever by default".
