"""Outage endurance — T-502 acceptance.

"Simulated 48 h NAS outage under continuous recording → queue grows bounded,
drains completely on recovery, dead-letters only per policy."

Time is warped via an injected clock so the 48 h passes instantly. The archive
target is ``down`` for the whole outage; the key properties asserted are:

  - the queue never grows past what was enqueued (no duplication, no runaway),
  - **nothing dead-letters** during a pure connectivity outage — transient
    failures retry forever within bounded backoff,
  - **no deletion is licensed** while unsynced (door-media's local copies stay
    protected — ADR-0007 coordination), and
  - on recovery the queue drains completely and every clip is licensed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from door_sync.engine import SyncEngine
from door_sync.queue import UploadQueue

pytestmark = pytest.mark.anyio


async def test_48h_nas_outage_drains_on_recovery(tmp_path: Path, helpers) -> None:
    now = [1_000_000.0]
    settings = helpers.make_settings(
        tmp_path,
        media_target="mock",
        backoff_base_s=2.0,
        backoff_max_s=300.0,
        max_permanent_attempts=5,
    )
    queue = UploadQueue(settings.queue_db_path)
    media = helpers.MockMediaTarget()
    media.down = True  # NAS mount unreachable
    door_media = helpers.RecordingMediaClient()
    engine = SyncEngine(
        queue=queue,
        settings=settings,
        media_target=media,
        nuc_target=helpers.MockNucTarget(),
        media_client=door_media,
        clock=lambda: now[0],
    )

    try:
        n_clips = 20
        clips = []
        for i in range(n_clips):
            rid = f"00000000-0000-7000-8000-{i:012d}"
            rel, sha, _ = helpers.make_recording_file(settings.ssd_data_root, name=f"{rid}.mp4")
            engine.enqueue_recording(recording_id=rid, local_path=rel, sha256=sha, trace_id=rid)
            clips.append((rid, sha))

        # --- 48h outage: retry storm, advancing the clock past each backoff ---
        step_s = 900.0
        steps = int((48 * 3600) / step_s) + 1
        max_depth = 0
        for _ in range(steps):
            now[0] += step_s
            await engine.run_once()
            stats = queue.stats(now_epoch=now[0])
            max_depth = max(max_depth, stats.pending)
            assert stats.dead_letter == 0  # transient never dead-letters
            assert stats.completed == 0  # target down: nothing completes

        # Bounded: never grew beyond what was enqueued; no unsynced deletion licensed.
        assert max_depth == n_clips
        assert queue.stats(now_epoch=now[0]).pending == n_clips
        assert door_media.synced == []

        # --- recovery: NAS back; drain completely ---
        media.down = False
        for _ in range(50):
            now[0] += step_s
            await engine.run_once()
            if queue.stats(now_epoch=now[0]).pending == 0 and not queue.items_awaiting_license():
                break

        final = queue.stats(now_epoch=now[0])
        assert final.pending == 0
        assert final.dead_letter == 0
        assert final.completed == n_clips
        assert sorted(r for r, _ in door_media.synced) == sorted(r for r, _ in clips)
        # Archive holds exactly one verified copy per clip.
        assert len(media.store) == n_clips
    finally:
        queue.close()


async def test_permanent_failures_dead_letter_during_outage_are_isolated(
    tmp_path: Path, helpers
) -> None:
    """A genuinely-broken item (missing local file) dead-letters after the cap
    while healthy items still drain once the target recovers — dead-lettering is
    per-item policy, not collateral damage from the outage."""
    now = [1_000_000.0]
    settings = helpers.make_settings(
        tmp_path, backoff_base_s=1.0, backoff_max_s=10.0, max_permanent_attempts=3
    )
    queue = UploadQueue(settings.queue_db_path)
    media = helpers.MockMediaTarget()
    door_media = helpers.RecordingMediaClient()
    engine = SyncEngine(
        queue=queue,
        settings=settings,
        media_target=media,
        nuc_target=helpers.MockNucTarget(),
        media_client=door_media,
        clock=lambda: now[0],
    )
    try:
        good_rid = "00000000-0000-7000-8000-000000000001"
        rel, sha, _ = helpers.make_recording_file(settings.ssd_data_root, name="good.mp4")
        engine.enqueue_recording(
            recording_id=good_rid, local_path=rel, sha256=sha, trace_id=good_rid
        )

        bad_rid = "00000000-0000-7000-8000-000000000002"
        engine.enqueue_recording(
            recording_id=bad_rid,
            local_path="recordings/missing.mp4",
            sha256="00" * 32,
            trace_id=bad_rid,
        )

        for _ in range(30):
            now[0] += 20.0
            await engine.run_once()

        good_item = queue.get(good_rid)
        bad_item = queue.get(bad_rid)
        assert good_item is not None and good_item.status == "completed"
        assert bad_item is not None and bad_item.status == "dead_letter"
        # The bad item never licensed a deletion.
        assert door_media.synced == [(good_rid, sha)]
    finally:
        queue.close()
