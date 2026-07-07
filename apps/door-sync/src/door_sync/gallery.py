"""Private photo gallery store used by door-sync.

The gallery is an archive-side projection for approved photo-booth stills. It
does not create public/social posts; it only records owner approval, manual
tags, optional wallboard eligibility, and deterministic NAS album paths.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from door_sync.fence import resolve_syncable


def _utcnow() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _album_for(now_iso: str) -> str:
    return now_iso[:7]


@dataclass(frozen=True, slots=True)
class GalleryPhotoInput:
    recording_id: str
    local_path: str
    thumbnail_path: str | None
    consent_metadata_path: str | None
    sha256: str
    tags: tuple[str, ...] = ()
    approved_by: str = "owner"
    wallboard_moment: bool = False


@dataclass(slots=True)
class GalleryPhoto:
    recording_id: str
    status: str
    album: str
    original_path: str
    thumbnail_path: str | None
    consent_metadata_path: str | None
    gallery_original_path: str | None
    gallery_thumbnail_path: str | None
    gallery_metadata_path: str | None
    sha256: str
    tags: list[str] = field(default_factory=list)
    approved_by: str = "owner"
    approved_at: str | None = None
    wallboard_moment: bool = False
    deleted_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class GalleryStore(Protocol):
    def ingest_approved_photo(self, photo: GalleryPhotoInput) -> GalleryPhoto: ...
    def list_photos(self, *, include_deleted: bool = False) -> list[GalleryPhoto]: ...
    def list_wallboard_moments(self) -> list[GalleryPhoto]: ...
    def update_tags(
        self, recording_id: str, *, tags: tuple[str, ...], wallboard_moment: bool | None
    ) -> GalleryPhoto | None: ...
    def delete_photo(self, recording_id: str) -> bool: ...


class FilesystemGalleryStore:
    """NAS-backed private gallery with a small JSON manifest.

    ``nas_root`` models a mounted share. Source paths are SSD-relative and must
    pass the same sync fence as media uploads.
    """

    def __init__(self, *, nas_root: Path, ssd_data_root: Path, syncable_roots: tuple[str, ...]):
        self._nas_root = nas_root
        self._ssd_data_root = ssd_data_root
        self._syncable_roots = syncable_roots
        self._manifest_path = nas_root / "gallery" / "manifest.json"

    def ingest_approved_photo(self, photo: GalleryPhotoInput) -> GalleryPhoto:
        self._ensure_nas()
        existing = self._load().get(photo.recording_id)
        approved_at = existing.approved_at if existing and existing.approved_at else _utcnow()
        album = existing.album if existing else _album_for(approved_at)
        base = Path("gallery") / "albums" / album
        gallery_original = str(base / "photos" / f"{photo.recording_id}.jpg")
        gallery_thumb = str(base / "thumbnails" / f"{photo.recording_id}.jpg")
        gallery_metadata = str(base / "metadata" / f"{photo.recording_id}.json")

        original_src = resolve_syncable(
            photo.local_path,
            ssd_data_root=self._ssd_data_root,
            syncable_roots=self._syncable_roots,
        )
        self._copy(original_src, self._nas_root / gallery_original)

        thumb_gallery_path: str | None = None
        if photo.thumbnail_path:
            thumb_src = resolve_syncable(
                photo.thumbnail_path,
                ssd_data_root=self._ssd_data_root,
                syncable_roots=self._syncable_roots,
            )
            self._copy(thumb_src, self._nas_root / gallery_thumb)
            thumb_gallery_path = gallery_thumb

        metadata_gallery_path: str | None = None
        if photo.consent_metadata_path:
            metadata_src = resolve_syncable(
                photo.consent_metadata_path,
                ssd_data_root=self._ssd_data_root,
                syncable_roots=self._syncable_roots,
            )
            self._copy(metadata_src, self._nas_root / gallery_metadata)
            metadata_gallery_path = gallery_metadata

        row = GalleryPhoto(
            recording_id=photo.recording_id,
            status="approved",
            album=album,
            original_path=photo.local_path,
            thumbnail_path=photo.thumbnail_path,
            consent_metadata_path=photo.consent_metadata_path,
            gallery_original_path=gallery_original,
            gallery_thumbnail_path=thumb_gallery_path,
            gallery_metadata_path=metadata_gallery_path,
            sha256=photo.sha256,
            tags=list(photo.tags),
            approved_by=photo.approved_by,
            approved_at=approved_at,
            wallboard_moment=photo.wallboard_moment,
            deleted_at=None,
        )
        manifest = self._load()
        manifest[photo.recording_id] = row
        self._save(manifest)
        return row

    def list_photos(self, *, include_deleted: bool = False) -> list[GalleryPhoto]:
        photos = list(self._load().values())
        if not include_deleted:
            photos = [p for p in photos if p.status != "deleted"]
        photos.sort(key=lambda p: p.approved_at or "", reverse=True)
        return photos

    def list_wallboard_moments(self) -> list[GalleryPhoto]:
        return [
            p
            for p in self.list_photos()
            if p.status == "approved" and p.wallboard_moment and p.approved_by == "owner"
        ]

    def update_tags(
        self, recording_id: str, *, tags: tuple[str, ...], wallboard_moment: bool | None
    ) -> GalleryPhoto | None:
        manifest = self._load()
        row = manifest.get(recording_id)
        if row is None or row.status == "deleted":
            return None
        row.tags = list(tags)
        if wallboard_moment is not None:
            row.wallboard_moment = wallboard_moment
        manifest[recording_id] = row
        self._save(manifest)
        return row

    def delete_photo(self, recording_id: str) -> bool:
        manifest = self._load()
        row = manifest.get(recording_id)
        paths: list[str] = []
        if row is not None:
            paths.extend(
                p
                for p in (
                    row.original_path,
                    row.thumbnail_path,
                    row.consent_metadata_path,
                    row.gallery_original_path,
                    row.gallery_thumbnail_path,
                    row.gallery_metadata_path,
                )
                if p
            )
            row.status = "deleted"
            row.deleted_at = row.deleted_at or _utcnow()
            row.wallboard_moment = False
            manifest[recording_id] = row
            self._save(manifest)

        # Also remove deterministic base archive paths for repeated delivery or
        # deletion-before-ingestion races where the manifest row may be absent.
        paths.extend(
            [
                f"recordings/photo_booth_{recording_id}.jpg",
                f"recordings/photo_booth_{recording_id}.consent.json",
                f"thumbnails/photo_booth_{recording_id}.jpg",
            ]
        )
        deleted_any = row is not None
        for rel in set(paths):
            target = self._nas_root / rel
            if target.exists():
                target.unlink()
                deleted_any = True
        return deleted_any

    def _ensure_nas(self) -> None:
        if not self._nas_root.exists() or not self._nas_root.is_dir():
            msg = f"NAS gallery root is unavailable: {self._nas_root}"
            raise FileNotFoundError(msg)

    def _copy(self, src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        shutil.copyfile(src, tmp)
        tmp.replace(dest)

    def _load(self) -> dict[str, GalleryPhoto]:
        if not self._manifest_path.exists():
            return {}
        raw = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        return {k: GalleryPhoto(**v) for k, v in raw.items()}

    def _save(self, manifest: dict[str, GalleryPhoto]) -> None:
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v.to_dict() for k, v in manifest.items()}
        self._manifest_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


class MockGalleryStore:
    """In-memory gallery store for dev/tests without a mounted NAS."""

    def __init__(self) -> None:
        self.photos: dict[str, GalleryPhoto] = {}
        self.deleted: set[str] = set()

    def ingest_approved_photo(self, photo: GalleryPhotoInput) -> GalleryPhoto:
        approved_at = _utcnow()
        row = GalleryPhoto(
            recording_id=photo.recording_id,
            status="approved",
            album=_album_for(approved_at),
            original_path=photo.local_path,
            thumbnail_path=photo.thumbnail_path,
            consent_metadata_path=photo.consent_metadata_path,
            gallery_original_path=f"gallery/mock/{photo.recording_id}.jpg",
            gallery_thumbnail_path=(
                f"gallery/mock/{photo.recording_id}.thumb.jpg" if photo.thumbnail_path else None
            ),
            gallery_metadata_path=(
                f"gallery/mock/{photo.recording_id}.json" if photo.consent_metadata_path else None
            ),
            sha256=photo.sha256,
            tags=list(photo.tags),
            approved_by=photo.approved_by,
            approved_at=approved_at,
            wallboard_moment=photo.wallboard_moment,
        )
        self.photos[photo.recording_id] = row
        return row

    def list_photos(self, *, include_deleted: bool = False) -> list[GalleryPhoto]:
        photos = list(self.photos.values())
        if not include_deleted:
            photos = [p for p in photos if p.status != "deleted"]
        return photos

    def list_wallboard_moments(self) -> list[GalleryPhoto]:
        return [
            p
            for p in self.list_photos()
            if p.status == "approved" and p.wallboard_moment and p.approved_by == "owner"
        ]

    def update_tags(
        self, recording_id: str, *, tags: tuple[str, ...], wallboard_moment: bool | None
    ) -> GalleryPhoto | None:
        row = self.photos.get(recording_id)
        if row is None or row.status == "deleted":
            return None
        row.tags = list(tags)
        if wallboard_moment is not None:
            row.wallboard_moment = wallboard_moment
        return row

    def delete_photo(self, recording_id: str) -> bool:
        row = self.photos.get(recording_id)
        self.deleted.add(recording_id)
        if row is None:
            return False
        row.status = "deleted"
        row.deleted_at = row.deleted_at or _utcnow()
        row.wallboard_moment = False
        return True
