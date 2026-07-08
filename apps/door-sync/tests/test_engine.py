"""SyncEngine behaviour: media archive, event mirror, purge, integrity."""

from __future__ import annotations

from pathlib import Path

import pytest
from door_sync.emitter import reset_broadcast_queue
from door_sync.engine import SyncEngine
from door_sync.queue import UploadQueue
from door_sync.targets import sha256_file

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _fresh_broadcast_queue():
    reset_broadcast_queue()
    yield
    reset_broadcast_queue()


async def test_clip_upload_verifies_then_licenses_deletion(engine_env, helpers) -> None:
    engine, queue, media, _nuc, media_client, settings = engine_env
    rel, sha, _ = helpers.make_recording_file(settings.ssd_data_root)
    rid = "00000000-0000-7000-8000-00000000aa01"

    engine.enqueue_recording(recording_id=rid, local_path=rel, sha256=sha, trace_id=rid)
    await engine.run_once()

    item = queue.get(rid)
    assert item.status == "completed"
    assert item.verified_sha256 == sha
    assert item.licensed == 1
    # The archive holds exactly one copy at the deterministic key.
    assert media.store == {rel: sha}
    # door-media was licensed to delete AFTER verification — never before.
    assert media_client.synced == [(rid, sha)]


async def test_event_mirror_is_idempotent(engine_env, helpers) -> None:
    engine, queue, _media, nuc, _mc, _settings = engine_env
    ev = helpers.make_session_event_dict()
    assert engine.enqueue_event(ev) is True
    # Same event delivered twice (SSE replay / crash) → single queue row.
    assert engine.enqueue_event(ev) is False
    await engine.run_once()
    assert queue.get(ev["event_id"]).status == "completed"
    assert nuc.events[ev["event_id"]]  # stored once
    assert len(nuc.events) == 1


async def test_purge_forwarded_to_nuc(engine_env) -> None:
    engine, queue, _media, nuc, _mc, _settings = engine_env
    engine.enqueue_purge(person_id="prs_abc", trace_id="00000000-0000-7000-8000-0000000000ff")
    await engine.run_once()
    assert nuc.purges == {"prs_abc": 1}
    items = [i for i in queue.list_items() if i.kind == "purge"]
    assert items[0].status == "completed"


async def test_thumbnail_upload_no_license_callback(engine_env, helpers) -> None:
    engine, queue, media, _nuc, media_client, settings = engine_env
    rel, _sha, _ = helpers.make_recording_file(settings.ssd_data_root, name="t.jpg")
    rid = "00000000-0000-7000-8000-00000000bb01"
    engine.enqueue_thumbnail(recording_id=rid, local_path=rel, trace_id=rid)
    await engine.run_once()
    from door_sync.engine import _thumb_item_id

    item = queue.get(_thumb_item_id(rid))
    assert item.status == "completed"
    assert rel in media.store
    # Thumbnails do not drive deletion licensing.
    assert media_client.synced == []


async def test_local_corruption_is_permanent_and_dead_letters(engine_env, helpers) -> None:
    engine, queue, media, _nuc, media_client, settings = engine_env
    rel, sha, abs_path = helpers.make_recording_file(settings.ssd_data_root)
    rid = "00000000-0000-7000-8000-00000000cc01"
    engine.enqueue_recording(recording_id=rid, local_path=rel, sha256=sha, trace_id=rid)
    # Corrupt the file on disk so the declared sha no longer matches.
    abs_path.write_bytes(b"tampered")

    for _ in range(settings.max_permanent_attempts):
        await engine.run_once()

    item = queue.get(rid)
    assert item.status == "dead_letter"
    # Never uploaded, never licensed a deletion.
    assert media.store == {}
    assert media_client.synced == []


async def test_missing_local_file_is_permanent(engine_env) -> None:
    engine, queue, _media, _nuc, _mc, settings = engine_env
    rid = "00000000-0000-7000-8000-00000000dd01"
    # Enqueue a path that passes the fence but does not exist on disk.
    engine.enqueue_recording(
        recording_id=rid, local_path="recordings/ghost.mp4", sha256="00" * 32, trace_id=rid
    )
    for _ in range(settings.max_permanent_attempts):
        await engine.run_once()
    assert queue.get(rid).status == "dead_letter"


async def test_upload_completed_event_emitted(engine_env, helpers) -> None:
    engine, _queue, _media, _nuc, _mc, settings = engine_env
    from door_sync.emitter import get_broadcast_queue

    rel, sha, _ = helpers.make_recording_file(settings.ssd_data_root)
    rid = "00000000-0000-7000-8000-00000000ee01"
    engine.enqueue_recording(recording_id=rid, local_path=rel, sha256=sha, trace_id=rid)
    await engine.run_once()

    q = get_broadcast_queue()
    types = []
    while not q.empty():
        types.append(q.get_nowait().type)
    assert "sync.upload_queued" in types
    assert "sync.upload_completed" in types


class _FlakyTarget:
    """Raises an *unclassified* error (not Transient/Permanent) on the first call."""

    def __init__(self) -> None:
        self.calls = 0

    async def upload_and_verify(
        self, *, local_path: Path, dest_key: str, expected_sha256: str
    ) -> str:
        self.calls += 1
        if self.calls == 1:
            msg = "unexpected boom"
            raise ValueError(msg)
        return sha256_file(local_path)


async def test_unexpected_error_is_transient_not_starving(tmp_path: Path, helpers) -> None:
    """A bug-shaped exception on one item must not abort the pass or starve the
    next item; it is treated as transient and retried."""
    settings = helpers.make_settings(tmp_path)
    queue = UploadQueue(settings.queue_db_path)
    try:
        engine = SyncEngine(
            queue=queue,
            settings=settings,
            media_target=_FlakyTarget(),
            nuc_target=helpers.MockNucTarget(),
            media_client=helpers.RecordingMediaClient(),
        )
        a = "00000000-0000-7000-8000-00000000fa01"
        b = "00000000-0000-7000-8000-00000000fb01"
        rela, sha_a, _ = helpers.make_recording_file(settings.ssd_data_root, name="a.mp4")
        relb, sha_b, _ = helpers.make_recording_file(settings.ssd_data_root, name="b.mp4")
        engine.enqueue_recording(recording_id=a, local_path=rela, sha256=sha_a, trace_id=a)
        engine.enqueue_recording(recording_id=b, local_path=relb, sha256=sha_b, trace_id=b)

        await engine.run_once()  # A explodes (transient), B still processed
        item_b = queue.get(b)
        assert item_b is not None and item_b.status == "completed"

        await engine.run_once()  # A retried, now succeeds
        item_a = queue.get(a)
        assert item_a is not None and item_a.status == "completed"
    finally:
        queue.close()


async def test_license_callback_error_leaves_item_completed_and_recoverable(
    tmp_path: Path, helpers
) -> None:
    """If the deletion-license callback raises *after* a verified upload, the item
    stays completed-but-unlicensed (never flipped back to pending) and a later
    finalize_licenses re-drives it — no premature deletion, no lost upload."""
    settings = helpers.make_settings(tmp_path)
    queue = UploadQueue(settings.queue_db_path)

    class _ExplodingClient:
        async def list_pending_clips(self) -> list[dict]:
            return []

        async def notify_synced(self, **_kw: object) -> None:
            msg = "kaboom"
            raise ValueError(msg)

    try:
        good = helpers.RecordingMediaClient()
        engine = SyncEngine(
            queue=queue,
            settings=settings,
            media_target=helpers.MockMediaTarget(),
            nuc_target=helpers.MockNucTarget(),
            media_client=_ExplodingClient(),
        )
        rid = "00000000-0000-7000-8000-00000000fc01"
        rel, sha, _ = helpers.make_recording_file(settings.ssd_data_root)
        engine.enqueue_recording(recording_id=rid, local_path=rel, sha256=sha, trace_id=rid)

        await engine.run_once()
        item = queue.get(rid)
        assert item is not None
        assert item.status == "completed"  # upload committed
        assert item.licensed == 0  # but NOT licensed for deletion

        # Recover with a working door-media client.
        engine._media_client = good  # type: ignore[attr-defined]  # noqa: SLF001
        await engine.finalize_licenses()
        item2 = queue.get(rid)
        assert item2 is not None and item2.licensed == 1
        assert good.synced == [(rid, sha)]
    finally:
        queue.close()


async def test_gallery_delete_removes_photo_across_nas_tiers(tmp_path: Path, helpers) -> None:
    from door_sync.gallery import FilesystemGalleryStore, GalleryPhotoInput

    settings = helpers.make_settings(tmp_path, media_target="nas")
    nas_root = Path(settings.nas_sync_target)
    nas_root.mkdir(parents=True)
    rid = "00000000-0000-7000-8000-00000000d606"

    photo_rel, sha, photo_abs = helpers.make_recording_file(
        settings.ssd_data_root, name=f"photo_booth_{rid}.jpg"
    )
    thumb_rel = f"thumbnails/photo_booth_{rid}.jpg"
    thumb_abs = settings.ssd_data_root / thumb_rel
    thumb_abs.parent.mkdir(parents=True, exist_ok=True)
    thumb_abs.write_bytes(b"thumb-bytes")
    metadata_rel = f"recordings/photo_booth_{rid}.consent.json"
    metadata_abs = settings.ssd_data_root / metadata_rel
    metadata_abs.write_text('{"consent_context":"visitor_initiated"}', encoding="utf-8")

    for rel, src in (
        (photo_rel, photo_abs),
        (thumb_rel, thumb_abs),
        (metadata_rel, metadata_abs),
    ):
        dest = nas_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())

    store = FilesystemGalleryStore(
        nas_root=nas_root,
        ssd_data_root=settings.ssd_data_root,
        syncable_roots=settings.syncable_roots,
    )
    approved = store.ingest_approved_photo(
        GalleryPhotoInput(
            recording_id=rid,
            local_path=photo_rel,
            thumbnail_path=thumb_rel,
            consent_metadata_path=metadata_rel,
            sha256=sha,
            tags=("friends",),
            wallboard_moment=False,
        )
    )
    gallery_paths = [
        approved.gallery_original_path,
        approved.gallery_thumbnail_path,
        approved.gallery_metadata_path,
    ]
    assert all(path is not None and (nas_root / path).exists() for path in gallery_paths)
    assert store.list_wallboard_moments() == []

    store.update_tags(rid, tags=("friends",), wallboard_moment=True)
    assert [p.recording_id for p in store.list_wallboard_moments()] == [rid]

    assert store.delete_photo(rid) is True
    assert not (nas_root / photo_rel).exists()
    assert not (nas_root / thumb_rel).exists()
    assert not (nas_root / metadata_rel).exists()
    for path in gallery_paths:
        assert path is not None
        assert not (nas_root / path).exists()
    assert store.list_wallboard_moments() == []
    deleted = store.list_photos(include_deleted=True)[0]
    assert deleted.status == "deleted"
