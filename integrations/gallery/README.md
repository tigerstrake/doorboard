# integrations/gallery — photo booth archive

Task: T-606 (Codex — media handling). Feature flag: `FEATURE_PHOTOBOOTH`.

- Photo booth capture is explicit and indoor (separate from door cameras); captures flow through door-media like any media artifact (SSD first, checksum, sync).
- This adapter manages the NAS-side gallery: approved-photo ingestion from door-sync, album structure, manual tagging (MVP), deletion honoring `social.deletion_requested`.
- Private sharing only in v1: gallery reachable to authenticated local users; public/social posting is deferred and would need its own ADR.
- Interface: `GalleryStore` with `nas | mock` implementations.
