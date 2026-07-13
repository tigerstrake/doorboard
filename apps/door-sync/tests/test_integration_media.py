"""Integration: the full ADR-0007 deletion-license chain against real door-media.

door-media (mock mode) finalizes a real clip → door-sync reconciles it from
``GET /recordings`` → uploads + checksum-verifies to the NAS archive → calls
door-media ``POST /internal/sync_completed`` → door-media flips the row to
``synced``, which is the *only* state its retention engine will delete. This
exercises the real HTTP contract on both sides, not a stand-in.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.anyio


async def test_finalized_clip_syncs_and_is_licensed_for_deletion(tmp_path: Path, helpers) -> None:
    from door_media import settings as media_settings
    from door_media.app import app as media_app
    from door_sync.engine import SyncEngine
    from door_sync.media_client import HttpMediaClient
    from door_sync.queue import UploadQueue
    from door_sync.targets import FilesystemNasTarget, MockNucTarget

    ssd = tmp_path / "ssd"
    media_admin_token = "integration-media-token"
    media_settings.override_settings(
        media_settings.Settings(
            DOOR_MEDIA_BIND="127.0.0.1:8082",
            MEDIA_MODE="mock",
            SSD_DATA_ROOT=ssd,
            DOOR_MEDIA_ADMIN_TOKEN=media_admin_token,
        )
    )
    try:
        async with media_app.router.lifespan_context(media_app):
            svc = media_app.state.service
            # Drive a real recording through door-media.
            sid = uuid4()
            trace = uuid4()
            rid = await svc.start_recording(session_id=sid, kind="bell_clip", trace_id=trace)
            assert rid is not None
            ok = await svc.finalize_recording(rid, consent_context="bell_event", trace_id=trace)
            assert ok
            row = svc._db.get(rid)  # noqa: SLF001 (test introspection)
            assert row is not None
            assert row.sync_status == "pending"
            assert row.path is not None

            # door-sync, pointed at door-media over ASGI.
            nas_root = tmp_path / "nas"
            nas_root.mkdir()
            settings = helpers.make_settings(
                tmp_path, media_target="nas", nas_sync_target=str(nas_root)
            )
            queue = UploadQueue(settings.queue_db_path)
            transport = httpx.ASGITransport(app=media_app)
            media_client = HttpMediaClient(
                "http://door-media",
                admin_token=media_admin_token,
                transport=transport,
            )
            engine = SyncEngine(
                queue=queue,
                settings=settings,
                media_target=FilesystemNasTarget(nas_root),
                nuc_target=MockNucTarget(),
                media_client=media_client,
            )
            try:
                # Reconcile picks up the finalized-but-unsynced clip, then drain.
                added = await engine.reconcile_from_media()
                assert added == 1
                await engine.run_once()

                # Archive holds a verified copy.
                dest = nas_root / row.path
                assert dest.exists()
                assert helpers.sha256_file(dest) == row.sha256

                # door-media now considers it synced → deletion is licensed.
                refreshed = svc._db.get(rid)  # noqa: SLF001
                assert refreshed is not None
                assert refreshed.sync_status == "synced"
                assert refreshed.synced_sha256 == row.sha256

                # Queue item completed and licensed.
                item = queue.get(str(rid))
                assert item is not None
                assert item.status == "completed"
                assert item.licensed == 1
            finally:
                queue.close()
    finally:
        media_settings.reset_settings()
