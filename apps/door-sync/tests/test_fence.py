"""Biometric fence — door-sync is structurally incapable of syncing identity data.

T-502 acceptance: "enrollment/embedding paths are structurally unreachable by
the sync engine (allowlist of syncable roots; test proves embeddings dir
excluded)."
"""

from __future__ import annotations

from pathlib import Path

import pytest
from door_sync.engine import SyncEngine
from door_sync.fence import FORBIDDEN_ROOTS, FenceViolation, resolve_syncable, validate_roots
from door_sync.queue import UploadQueue
from door_sync.settings import Settings

ROOTS = ("recordings", "thumbnails")


def test_valid_recording_path_accepted(tmp_path: Path) -> None:
    ssd = tmp_path / "ssd"
    resolved = resolve_syncable("recordings/2026/clip.mp4", ssd_data_root=ssd, syncable_roots=ROOTS)
    assert str(resolved).startswith(str(ssd.resolve()))


def test_enrollment_db_rejected(tmp_path: Path) -> None:
    ssd = tmp_path / "ssd"
    with pytest.raises(FenceViolation):
        resolve_syncable("visiond/enrollment.sqlite", ssd_data_root=ssd, syncable_roots=ROOTS)


def test_embeddings_dir_excluded_from_allowlist() -> None:
    assert "visiond" not in ROOTS
    assert FORBIDDEN_ROOTS & {"visiond", "embeddings", "enrollment", "tmp"}


def test_traversal_back_into_visiond_rejected(tmp_path: Path) -> None:
    ssd = tmp_path / "ssd"
    with pytest.raises(FenceViolation):
        resolve_syncable(
            "recordings/../visiond/enrollment.sqlite",
            ssd_data_root=ssd,
            syncable_roots=ROOTS,
        )


def test_absolute_path_outside_ssd_rejected(tmp_path: Path) -> None:
    ssd = tmp_path / "ssd"
    with pytest.raises(FenceViolation):
        resolve_syncable("/etc/passwd", ssd_data_root=ssd, syncable_roots=ROOTS)


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    ssd = tmp_path / "ssd"
    (ssd / "recordings").mkdir(parents=True)
    (ssd / "visiond").mkdir()
    secret = ssd / "visiond" / "enrollment.sqlite"
    secret.write_bytes(b"embeddings")
    link = ssd / "recordings" / "sneaky.mp4"
    link.symlink_to(secret)
    with pytest.raises(FenceViolation):
        resolve_syncable("recordings/sneaky.mp4", ssd_data_root=ssd, syncable_roots=ROOTS)


def test_validate_roots_rejects_forbidden() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        validate_roots(("recordings", "visiond"))


def test_validate_roots_rejects_empty_and_paths() -> None:
    with pytest.raises(ValueError, match="at least one"):
        validate_roots(())
    with pytest.raises(ValueError, match="single directory"):
        validate_roots(("recordings/nested",))


def test_engine_enqueue_refuses_biometric_path(tmp_path: Path, helpers) -> None:
    settings = helpers.make_settings(tmp_path)
    queue = UploadQueue(settings.queue_db_path)
    try:
        engine = SyncEngine(
            queue=queue,
            settings=settings,
            media_target=helpers.MockMediaTarget(),
            nuc_target=helpers.MockNucTarget(),
            media_client=helpers.RecordingMediaClient(),
        )
        with pytest.raises(FenceViolation):
            engine.enqueue_recording(
                recording_id="00000000-0000-7000-8000-000000000001",
                local_path="visiond/enrollment.sqlite",
                sha256="deadbeef",
                trace_id="00000000-0000-7000-8000-000000000002",
            )
        assert queue.list_items() == []
    finally:
        queue.close()


def test_default_settings_syncable_roots_exclude_identity(tmp_path: Path, helpers) -> None:
    settings = helpers.make_settings(tmp_path)
    assert "visiond" not in settings.syncable_roots
    assert set(settings.syncable_roots) == {"recordings", "thumbnails"}


def test_nas_mode_requires_explicit_absolute_target(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="NAS_SYNC_TARGET is required"):
        Settings(SSD_DATA_ROOT=tmp_path, SYNC_MEDIA_TARGET="nas", NAS_SYNC_TARGET="")

    with pytest.raises(ValueError, match="absolute path"):
        Settings(
            SSD_DATA_ROOT=tmp_path,
            SYNC_MEDIA_TARGET="nas",
            NAS_SYNC_TARGET="relative/nas",
        )


def test_sentinel_embedding_never_reaches_a_target(tmp_path: Path, helpers) -> None:
    """Sentinel-style proof (ADR-0009 P-1 spirit): a byte marker in the enrollment
    tree can never be handed to a target, because enqueue rejects the path first."""
    ssd = tmp_path / "ssd"
    (ssd / "visiond").mkdir(parents=True)
    sentinel = b"DOORBOARD_SENTINEL_" + b"x" * 13
    (ssd / "visiond" / "enrollment.sqlite").write_bytes(sentinel)

    settings = helpers.make_settings(tmp_path)
    queue = UploadQueue(settings.queue_db_path)
    media = helpers.MockMediaTarget()
    try:
        engine = SyncEngine(
            queue=queue,
            settings=settings,
            media_target=media,
            nuc_target=helpers.MockNucTarget(),
            media_client=helpers.RecordingMediaClient(),
        )
        with pytest.raises(FenceViolation):
            engine.enqueue_recording(
                recording_id="00000000-0000-7000-8000-000000000003",
                local_path="visiond/enrollment.sqlite",
                sha256="whatever",
                trace_id="00000000-0000-7000-8000-000000000004",
            )
        assert media.upload_calls == []
        assert media.store == {}
        # Sanity: a legitimate clip does enqueue.
        rel, sha, _ = helpers.make_recording_file(ssd)
        assert engine.enqueue_recording(
            recording_id="00000000-0000-7000-8000-000000000005",
            local_path=rel,
            sha256=sha,
            trace_id="00000000-0000-7000-8000-000000000006",
        )
    finally:
        queue.close()
