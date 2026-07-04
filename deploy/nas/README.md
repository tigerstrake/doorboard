# deploy/nas — archive configuration

The NAS is a durable archive, never an active database or recording target (ADR-0007).

- Shares/structure: `doorboard/clips/`, `doorboard/gallery/`, `doorboard/backups/` (Postgres dumps, config bundles).
- Access: one limited-scope service account for uploads (used by door-sync via the NUC where practical); admin credentials never leave the NAS/NUC.
- Backup verification: scheduled restore test documented in `docs/runbooks/` (an unverified backup is not a backup).
- Retention policy for archived clips defined with the owner during M5 (indefinite-by-default violates handoff "must not retain all raw media forever by default").
