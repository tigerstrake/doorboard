import asyncio
from uuid import uuid4

import pytest
from door_media.db import RecordingDB
from door_media.mock_router import MockMediaRouter
from door_media.service import RecordingService
from door_media.settings import settings


@pytest.fixture
def service_env(tmp_path):
    cfg = settings()
    db = RecordingDB(tmp_path / "test.db")
    router = MockMediaRouter(cfg)
    svc = RecordingService(router=router, db=db, settings=cfg)
    return svc, db, cfg


@pytest.mark.anyio
async def test_service_lifecycle(service_env):
    svc, db, cfg = service_env
    await svc.start()

    session_id = uuid4()
    trace_id = uuid4()

    # Start
    rid = await svc.start_recording(
        session_id=session_id,
        kind="video_message",
        trace_id=trace_id,
    )
    assert rid is not None
    assert rid in svc._active_handles

    row = db.get(rid)
    assert row is not None
    assert row.kind == "video_message"

    # Wait a moment
    await asyncio.sleep(0.1)

    # Finalize
    ok = await svc.finalize_recording(
        rid,
        consent_context="visitor_initiated",
        trace_id=trace_id,
    )
    assert ok
    assert rid not in svc._active_handles

    row = db.get(rid)
    assert row.path is not None

    # Check thumbnail stub
    thumb_path = cfg.ssd_data_root / row.thumbnail_path
    assert thumb_path.exists()

    # Sync complete
    svc.on_sync_upload_completed(recording_id=rid, verified_sha256=row.sha256)
    row = db.get(rid)
    assert row.sync_status == "synced"

    # Delete requested
    deleted = svc.on_deletion_requested(recording_id=rid, trace_id=trace_id)
    assert deleted
    row = db.get(rid)
    assert row.sync_status == "deleted"

    assert not (cfg.ssd_data_root / row.path).exists()
    assert not thumb_path.exists()

    await svc.stop()


@pytest.mark.anyio
async def test_active_recording_limit_refuses_new_windows(service_env):
    svc, _db, cfg = service_env
    cfg.max_active_recordings = 1
    await svc.start()

    first = await svc.start_recording(session_id=uuid4(), kind="bell_clip", trace_id=uuid4())
    assert first is not None
    assert await svc.start_recording(session_id=uuid4(), kind="bell_clip", trace_id=uuid4()) is None

    assert await svc.discard_recording(first, trace_id=uuid4())
    assert (
        await svc.start_recording(session_id=uuid4(), kind="bell_clip", trace_id=uuid4())
        is not None
    )
    await svc.stop()


@pytest.mark.anyio
async def test_thumbnail_failure_does_not_block_finalization(service_env):
    svc, db, cfg = service_env
    await svc.start()

    session_id = uuid4()
    trace_id = uuid4()

    rid = await svc.start_recording(
        session_id=session_id,
        kind="video_message",
        trace_id=trace_id,
    )
    assert rid is not None

    # Simulate a corrupted/missing video file to trigger thumbnail failure.
    # We monkeypatch _generate_thumbnail to return False.
    async def mock_gen(*args, **kwargs):
        return False

    svc._generate_thumbnail = mock_gen

    ok = await svc.finalize_recording(
        rid,
        consent_context="visitor_initiated",
        trace_id=trace_id,
    )
    assert ok  # Finalization still succeeds!

    row = db.get(rid)
    assert row.thumbnail_path is None  # Marked missing!

    await svc.stop()


@pytest.mark.anyio
async def test_finalize_failure_keeps_handle_retryable(service_env):
    svc, _db, _cfg = service_env
    await svc.start()
    recording_id = await svc.start_recording(
        session_id=uuid4(),
        kind="video_message",
        trace_id=uuid4(),
    )
    assert recording_id is not None
    original_finalize = svc._router.finalize_recording
    attempts = 0

    async def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient finalization failure")
        return await original_finalize(*args, **kwargs)

    svc._router.finalize_recording = fail_once
    assert not await svc.finalize_recording(
        recording_id,
        consent_context="visitor_initiated",
        trace_id=uuid4(),
    )
    assert recording_id in svc._active_handles
    assert await svc.finalize_recording(
        recording_id,
        consent_context="visitor_initiated",
        trace_id=uuid4(),
    )
    assert recording_id not in svc._active_handles
    await svc.stop()


@pytest.mark.anyio
async def test_db_finalize_failure_reuses_completed_router_result(service_env):
    svc, db, _cfg = service_env
    await svc.start()
    recording_id = await svc.start_recording(
        session_id=uuid4(),
        kind="video_message",
        trace_id=uuid4(),
    )
    assert recording_id is not None

    router_finalize = svc._router.finalize_recording
    router_calls = 0

    async def count_router_calls(*args, **kwargs):
        nonlocal router_calls
        router_calls += 1
        return await router_finalize(*args, **kwargs)

    db_update = db.update_finalized
    db_calls = 0

    def fail_db_once(*args, **kwargs):
        nonlocal db_calls
        db_calls += 1
        if db_calls == 1:
            raise OSError("transient sqlite failure")
        return db_update(*args, **kwargs)

    svc._router.finalize_recording = count_router_calls
    db.update_finalized = fail_db_once

    assert not await svc.finalize_recording(
        recording_id,
        consent_context="visitor_initiated",
        trace_id=uuid4(),
    )
    assert recording_id in svc._active_handles
    assert await svc.finalize_recording(
        recording_id,
        consent_context="visitor_initiated",
        trace_id=uuid4(),
    )
    assert router_calls == 1
    assert db_calls == 2
    assert recording_id not in svc._active_handles
    await svc.stop()


@pytest.mark.anyio
async def test_retention_respects_per_kind_policy(service_env):
    svc, db, cfg = service_env
    await svc.start()

    # Override settings for testing
    cfg.bell_clip_max_age_s = 1  # 1 second age cap
    cfg.video_message_max_age_s = 1000  # 1000 seconds age cap
    cfg.bell_clip_max_size_bytes = 100 * 1024 * 1024  # 100 MB
    cfg.video_message_max_size_bytes = 100 * 1024 * 1024  # 100 MB

    session_id = uuid4()
    trace_id = uuid4()

    # Create a bell clip
    rid_bell = await svc.start_recording(
        session_id=session_id,
        kind="bell_clip",
        trace_id=trace_id,
    )
    await svc.finalize_recording(rid_bell, consent_context="bell_event", trace_id=trace_id)
    # Give it a small size so it fits the size cap but we can test age cap
    # The mock router writes size proportional to elapsed time. Let's make it synced.
    row_bell = db.get(rid_bell)
    svc.on_sync_upload_completed(recording_id=rid_bell, verified_sha256=row_bell.sha256)

    # Create a video message
    rid_video = await svc.start_recording(
        session_id=session_id,
        kind="video_message",
        trace_id=trace_id,
    )
    await svc.finalize_recording(rid_video, consent_context="visitor_initiated", trace_id=trace_id)
    row_video = db.get(rid_video)
    svc.on_sync_upload_completed(recording_id=rid_video, verified_sha256=row_video.sha256)

    # Wait 2 seconds so the bell clip is expired (> 1s) but video message is not (< 1000s)
    import asyncio

    await asyncio.sleep(1.5)

    # Run retention pass
    await svc._run_retention_pass()

    # Verify: bell_clip is deleted (expired), video_message is preserved
    assert db.get(rid_bell).sync_status == "deleted"
    assert db.get(rid_video).sync_status == "synced"

    await svc.stop()


@pytest.mark.anyio
async def test_retention_respects_size_caps(service_env):
    svc, db, cfg = service_env
    await svc.start()

    # Set a low size cap for photo_booth (e.g. 150 KB)
    cfg.photo_booth_max_size_bytes = 150 * 1024

    # We will write two synced photo_booth clips. Each mock clip is ~512 KB/s.
    # Let's create the first clip, finalize, and sync.
    session_id = uuid4()
    trace_id = uuid4()

    rid1 = await svc.start_recording(session_id=session_id, kind="photo_booth", trace_id=trace_id)
    await asyncio.sleep(0.2)  # Will be ~100 KB
    await svc.finalize_recording(rid1, consent_context="visitor_initiated", trace_id=trace_id)
    row1 = db.get(rid1)
    svc.on_sync_upload_completed(recording_id=rid1, verified_sha256=row1.sha256)

    # Create the second clip, finalize, and sync
    rid2 = await svc.start_recording(session_id=session_id, kind="photo_booth", trace_id=trace_id)
    await asyncio.sleep(0.2)
    await svc.finalize_recording(rid2, consent_context="visitor_initiated", trace_id=trace_id)
    row2 = db.get(rid2)
    svc.on_sync_upload_completed(recording_id=rid2, verified_sha256=row2.sha256)

    # Run retention pass
    await svc._run_retention_pass()

    # Verify: The oldest one (rid1) is deleted to satisfy the size cap, rid2 remains synced
    assert db.get(rid1).sync_status == "deleted"
    assert db.get(rid2).sync_status == "synced"

    await svc.stop()
