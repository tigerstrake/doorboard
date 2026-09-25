# integrations/gallery — photo booth archive

Task: T-606 (Codex — media handling). Feature flag: `FEATURE_PHOTOBOOTH`.

**There is no code in this directory.** The gallery adapter this brief
describes was implemented inside door-sync, where the NAS archive lives:
[`apps/door-sync/src/door_sync/gallery.py`](../../apps/door-sync/src/door_sync/gallery.py).

- `GalleryStore` — the interface (a `Protocol`): `ingest_approved_photo`,
  `list_photos`, `list_wallboard_moments`, `update_tags`, `delete_photo`.
- `FilesystemGalleryStore` — the NAS implementation. Approval copies an
  already-saved `photo_booth` artifact into `gallery/albums/YYYY-MM/` with
  optional manual tags and a wallboard-moment flag; it refuses to write unless
  the NAS mount marker is present.
- `MockGalleryStore` — in-memory, for dev and CI (hardware-optional rule).

What the brief asked for, and where it holds:

- Photo booth capture is explicit and indoor (separate from door cameras);
  captures flow through door-media like any media artifact (SSD first,
  checksum, sync).
- Approved-photo ingestion from door-sync, album structure, manual tagging
  (MVP), and deletion honoring `social.deletion_requested` with
  `target_kind="photo"` — which removes NAS base copies, album copies,
  thumbnails and consent metadata (door-api also asks door-media to remove
  the SSD copy).
- Private sharing only in v1: the gallery is an archive-side projection
  reachable to authenticated local users. Wallboard moments are returned
  only when owner-approved *and* explicitly marked. Public/social posting
  is deferred and would need its own ADR.

See [`apps/door-sync/README.md`](../../apps/door-sync/README.md) for the
endpoints and the sync targets it sits beside.
