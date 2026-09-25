import asyncio
import hashlib
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from door_media.db import RecordingDB
from door_media.mediamtx_router import MediaMTXRouter
from door_media.mock_router import MockMediaRouter
from door_media.service import RecordingService
from door_media.settings import Settings, settings


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
async def test_active_recording_limit_bounds_open_windows_not_failed_ones(service_env):
    """The cap is for windows that are genuinely open. A window whose finalize
    failed is over, so it must not count against the next bell press."""
    svc, _db, cfg = service_env
    cfg.max_active_recordings = 1
    await svc.start()

    first = await svc.start_recording(session_id=uuid4(), kind="bell_clip", trace_id=uuid4())
    assert first is not None
    assert await svc.start_recording(session_id=uuid4(), kind="bell_clip", trace_id=uuid4()) is None

    async def cannot_cut(*args, **kwargs):
        raise RuntimeError("no media segments found")

    svc._router.finalize_recording = cannot_cut
    assert not await svc.finalize_recording(first, consent_context="bell_event", trace_id=uuid4())

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
async def test_router_finalize_failure_releases_the_window(service_env):
    """A recording the router cannot cut is over, not active.

    The old behaviour kept the handle "for a retry". Nothing ever retried it:
    door-api's outbox gave up after its attempts, and the segments that would
    have made the clip were never written. The handle just sat there, pinning
    segment pruning and consuming the active cap.
    """
    svc, db, _cfg = service_env
    await svc.start()
    recording_id = await svc.start_recording(
        session_id=uuid4(),
        kind="video_message",
        trace_id=uuid4(),
    )
    assert recording_id is not None
    router = svc._router

    async def cannot_cut(*args, **kwargs):
        raise RuntimeError("no media segments found")

    router.finalize_recording = cannot_cut
    assert not await svc.finalize_recording(
        recording_id,
        consent_context="visitor_initiated",
        trace_id=uuid4(),
    )

    assert recording_id not in svc._active_handles
    assert recording_id not in router._active
    assert db.get(recording_id) is None, "a started row with no clip is an orphan"
    assert svc.finalize_failures == 1
    # And a later finalize for the same id is the unknown-recording no-op, not a crash.
    assert not await svc.finalize_recording(
        recording_id,
        consent_context="visitor_initiated",
        trace_id=uuid4(),
    )
    await svc.stop()


@pytest.mark.anyio
async def test_a_camera_outage_does_not_lock_the_door_out_of_recording(tmp_path: Path):
    """The lockout as it happened on the door, against the real MediaMTX router.

    Eight bell presses while MediaMTX had no publisher: each start_recording
    registered a window, each finalize found no segments and raised. Before the
    fix the eight leaked handles hit max_active_recordings, so the ninth press --
    camera long since back -- was refused, and stayed refused until restart.
    Meanwhile prune_segments clamped its cutoff to the oldest leaked window and
    the rolling segments grew without bound.
    """
    cfg = Settings(
        SSD_DATA_ROOT=tmp_path,
        MEDIAMTX_CONFIG_PATH=tmp_path / "mediamtx.yml",
        MEDIA_MODE="mediamtx",
        DOOR_MEDIA_MAX_ACTIVE_RECORDINGS=8,
        DOOR_MEDIA_SEGMENT_RETENTION_S=60,
    )
    router = MediaMTXRouter(cfg)  # never started: no MediaMTX process, no segments
    db = RecordingDB(tmp_path / "test.db")
    svc = RecordingService(router=router, db=db, settings=cfg)
    await svc.start()
    try:
        for _ in range(cfg.max_active_recordings):
            rid = await svc.start_recording(session_id=uuid4(), kind="bell_clip", trace_id=uuid4())
            assert rid is not None
            assert not await svc.finalize_recording(
                rid, consent_context="bell_event", trace_id=uuid4()
            )
            assert rid not in router._active
            assert rid not in svc._active_handles

        assert svc.finalize_failures == cfg.max_active_recordings
        assert db.list_pending() == [], "no orphan rows"

        # Pruning is not pinned: a segment older than retention goes.
        now = time.time()
        old_segment = cfg.segments_root / "visitor" / "old.mp4"
        old_segment.parent.mkdir(parents=True, exist_ok=True)
        old_segment.write_bytes(b"segment")
        os.utime(old_segment, (now - 3600, now - 3600))
        assert router.prune_segments(now_epoch=now) == 1
        assert not old_segment.exists()

        # The ninth press records.
        ninth = await svc.start_recording(session_id=uuid4(), kind="bell_clip", trace_id=uuid4())
        assert ninth is not None
        assert ninth in router._active
    finally:
        await svc.stop()
        await router.stop()
        db.close()


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


@pytest.mark.anyio
async def test_concurrent_finalize_cuts_the_clip_once(service_env, monkeypatch):
    """Overlapping finalize calls must produce one cut, one hash, one event.

    Regression: the ``_pending_finalized`` reuse check straddles an await, so
    three concurrent callers all ran the router against the same out_path and
    each emitted its own finalized event with a different sha256. door-sync
    archived the first hash while the last writer won on disk, so every clip
    dead-lettered as "local checksum mismatch" and nothing reached the NAS.
    """
    svc, db, cfg = service_env
    await svc.start()
    recording_id = await svc.start_recording(
        session_id=uuid4(),
        kind="video_message",
        trace_id=uuid4(),
    )
    assert recording_id is not None
    await asyncio.sleep(0.1)

    router_finalize = svc._router.finalize_recording
    router_calls = 0

    async def slow_router(*args, **kwargs):
        # A real finalize is a concat + remux (~1s). That window is the bug:
        # without serialisation every caller gets past the reuse check.
        nonlocal router_calls
        router_calls += 1
        await asyncio.sleep(0.15)
        return await router_finalize(*args, **kwargs)

    svc._router.finalize_recording = slow_router
    emitted: list[dict] = []
    monkeypatch.setattr(
        "door_media.service.emit_recording_finalized",
        lambda **kwargs: emitted.append(kwargs),
    )

    results = await asyncio.gather(
        *[
            svc.finalize_recording(
                recording_id,
                consent_context="visitor_initiated",
                trace_id=uuid4(),
            )
            for _ in range(3)
        ]
    )

    assert results == [True, True, True]
    assert router_calls == 1, f"clip was cut {router_calls} times"
    assert len(emitted) == 1, f"{len(emitted)} finalized events for one recording"

    row = db.get(recording_id)
    assert row.sha256 == emitted[0]["sha256"]

    # The whole point: what door-sync was told matches what is on disk.
    clip = cfg.ssd_data_root / row.path
    assert clip.exists()
    assert hashlib.sha256(clip.read_bytes()).hexdigest() == row.sha256

    assert recording_id not in svc._active_handles
    await svc.stop()


@pytest.mark.anyio
async def test_startup_sweeps_orphan_started_rows_older_than_segment_retention(service_env):
    """A crash or a failed finalize leaves a path IS NULL row; once older than the
    segment retention the segments for its clip are gone and it can only be an orphan."""
    from datetime import UTC, datetime, timedelta

    svc, db, cfg = service_env
    old_rid, fresh_rid = uuid4(), uuid4()
    for rid in (old_rid, fresh_rid):
        db.insert_started(
            recording_id=rid,
            session_id=uuid4(),
            kind="bell_clip",
            stream="visitor",
            started_mono_ms=0,
        )
    stale = datetime.now(UTC) - timedelta(seconds=cfg.segment_retention_s + 60)
    db._conn.execute(  # noqa: SLF001
        "UPDATE recordings SET started_at_utc=? WHERE recording_id=?",
        (stale.isoformat(), str(old_rid)),
    )
    db._conn.commit()  # noqa: SLF001

    await svc.start()
    try:
        assert db.get(old_rid) is None
        assert db.get(fresh_rid) is not None, "younger than retention: left alone"
    finally:
        await svc.stop()


@pytest.mark.anyio
async def test_thumbnail_ffmpeg_is_bounded(service_env, monkeypatch):
    """The only ffmpeg in door-media that ran unbounded, and it ran inside the
    per-recording finalize lock with door-api's request open behind it."""
    svc, _db, cfg = service_env
    cfg.media_mode = "mediamtx"
    clip = cfg.ssd_data_root / "recordings" / "clip.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"not really an mp4")

    class _Hang:
        returncode: int | None = None
        killed = False

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(3600)
            return (b"", b"")

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            return -9

    proc = _Hang()

    async def fake_exec(*args: object, **kwargs: object) -> _Hang:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("door_media.service._THUMBNAIL_FFMPEG_TIMEOUT_S", 0.05)

    started = time.monotonic()
    ok = await svc._generate_thumbnail(
        clip_path=clip, thumb_path=clip.with_suffix(".jpg"), duration_s=3.0
    )

    assert ok is False
    assert time.monotonic() - started < 5.0
    assert proc.killed, "a hung ffmpeg must be reaped, not abandoned"
