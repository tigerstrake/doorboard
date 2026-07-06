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
