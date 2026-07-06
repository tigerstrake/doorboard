import asyncio
from uuid import uuid4

import pytest
from door_media.mock_router import MockMediaRouter
from door_media.settings import settings


@pytest.mark.anyio
async def test_mock_router_lifecycle():
    cfg = settings()
    router = MockMediaRouter(cfg)

    session_id = uuid4()
    # Start recording
    handle = await router.start_recording(
        session_id=session_id,
        kind="bell_clip",
        stream="visitor",
    )

    assert handle.kind == "bell_clip"
    assert handle.session_id == session_id

    # Let time pass so file size is non-zero
    await asyncio.sleep(0.1)

    # Finalize
    finalized = await router.finalize_recording(handle, consent_context="bell_event")

    assert finalized.recording_id == handle.recording_id
    assert finalized.duration_s > 0
    assert finalized.size_bytes > 0
    assert finalized.sha256

    # Verify file exists where expected
    actual_path = cfg.ssd_data_root / finalized.path
    assert actual_path.exists()
    assert actual_path.stat().st_size == finalized.size_bytes


def test_mock_router_storage_status():
    cfg = settings()
    router = MockMediaRouter(cfg)

    status = router.storage_status()
    assert status.recording_allowed is True
    assert status.free_bytes > cfg.min_free_bytes
