"""Safety tests for tools/recover-stale-hash-clips.py.

That tool deliberately bypasses door-sync's pre-upload integrity check, so its
refusals matter more than its recoveries. These pin the four behaviours:

  - a valid clip with a stale hash is re-queued, in BOTH databases
  - a file whose stored hash already matches is left alone (different failure)
  - a file ffprobe can't decode is refused, not laundered into the archive
  - a missing file is abandoned, not silently retried forever

and that a dry run writes nothing at all.

Schemas are imported from the services rather than copied, so a schema change
breaks these tests instead of the tool silently writing to the wrong columns.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "recover-stale-hash-clips.py"

sys.path.insert(0, str(REPO / "apps/door-sync/src"))
sys.path.insert(0, str(REPO / "apps/door-media/src"))

from door_media.db import _SCHEMA as MEDIA_SCHEMA  # noqa: E402
from door_sync.queue import _SCHEMA as SYNC_SCHEMA  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None,
    reason="needs ffmpeg/ffprobe to build a genuinely decodable fixture",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_video(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=10",
            "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
        check=True,
    )
    return path


@pytest.fixture
def env(tmp_path: Path):
    ssd = tmp_path / "ssd"
    (ssd / "recordings").mkdir(parents=True)
    now = datetime.now(UTC).isoformat()

    good = _make_video(ssd / "recordings" / "bell_clip_good.mp4")
    matching = _make_video(ssd / "recordings" / "bell_clip_matching.mp4")
    corrupt = ssd / "recordings" / "bell_clip_corrupt.mp4"
    corrupt.write_bytes(b"not a video at all")

    sync_db = tmp_path / "queue.sqlite"
    media_db = tmp_path / "door_media.db"
    sync = sqlite3.connect(sync_db)
    sync.executescript(SYNC_SCHEMA)
    media = sqlite3.connect(media_db)
    media.executescript(MEDIA_SCHEMA)

    rows = [
        ("rec-good", "recordings/bell_clip_good.mp4", "f" * 64, "local checksum mismatch"),
        ("rec-matching", "recordings/bell_clip_matching.mp4", _sha(matching), "local checksum mismatch"),
        ("rec-corrupt", "recordings/bell_clip_corrupt.mp4", "a" * 64, "local checksum mismatch"),
        ("rec-gone", "recordings/bell_clip_gone.mp4", "b" * 64, "local media missing"),
    ]
    for item_id, rel, expected, err in rows:
        sync.execute(
            """INSERT INTO queue_item(item_id, kind, target, status, dest_key, recording_id,
                                      local_path, expected_sha256, attempts, permanent_failures,
                                      next_attempt_at, last_error, trace_id, created_at, updated_at)
               VALUES(?, 'clip', 'nas', 'dead_letter', ?, ?, ?, ?, 5, 5, 0, ?, 't', ?, ?)""",
            (item_id, rel, item_id, rel, expected, err, now, now),
        )
        media.execute(
            """INSERT INTO recordings(recording_id, session_id, kind, stream, started_at_utc,
                                      started_mono_ms, path, sha256, sync_status)
               VALUES(?, 'sess', 'bell_clip', 'visitor', '2026-07-20T15:11:03+00:00', 1, ?, ?, 'pending')""",
            (item_id, rel, expected),
        )
    sync.commit()
    media.commit()
    sync.close()
    media.close()
    return {"ssd": ssd, "sync_db": sync_db, "media_db": media_db, "good": good}


def _run(env, *extra) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, str(TOOL),
            "--ssd-root", str(env["ssd"]),
            "--sync-db", str(env["sync_db"]),
            "--media-db", str(env["media_db"]),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _status(db: Path, item_id: str) -> str:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT status FROM queue_item WHERE item_id=?", (item_id,)).fetchone()[0]
    finally:
        conn.close()


def _sync_status(db: Path, recording_id: str) -> str:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT sync_status FROM recordings WHERE recording_id=?", (recording_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_dry_run_writes_nothing(env):
    result = _run(env)
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    for item_id in ("rec-good", "rec-matching", "rec-corrupt", "rec-gone"):
        assert _status(env["sync_db"], item_id) == "dead_letter"
        assert _sync_status(env["media_db"], item_id) == "pending"


def test_apply_requeues_only_the_recoverable_clip(env):
    assert _run(env, "--apply").returncode == 0

    # Recovered: re-queued with the TRUE on-disk hash, retry state cleared.
    conn = sqlite3.connect(env["sync_db"])
    row = conn.execute(
        "SELECT status, expected_sha256, attempts, last_error FROM queue_item WHERE item_id='rec-good'"
    ).fetchone()
    conn.close()
    true_sha = _sha(env["good"])
    assert row[0] == "pending"
    assert row[1] == true_sha
    assert row[2] == 0
    assert row[3] is None

    # Everything else stays retired.
    assert _status(env["sync_db"], "rec-matching") == "dead_letter"
    assert _status(env["sync_db"], "rec-corrupt") == "dead_letter"
    assert _status(env["sync_db"], "rec-gone") == "dead_letter"


def test_both_databases_are_updated_so_mark_synced_can_apply(env):
    """The reason this tool touches two databases.

    door-media's mark_synced applies `WHERE recording_id=? AND sha256=?`. If its
    row kept the stale hash, the upload would succeed while the recording stayed
    sync_status='pending' forever — pinning oldest_unsynced_s at the very reading
    this is meant to clear.
    """
    assert _run(env, "--apply").returncode == 0
    true_sha = _sha(env["good"])

    conn = sqlite3.connect(env["media_db"])
    try:
        assert conn.execute(
            "SELECT sha256 FROM recordings WHERE recording_id='rec-good'"
        ).fetchone()[0] == true_sha

        applied = conn.execute(
            """UPDATE recordings SET sync_status='synced', synced_sha256=?
               WHERE recording_id='rec-good' AND sha256=? AND sync_status='pending'""",
            (true_sha, true_sha),
        ).rowcount
        assert applied == 1, "mark_synced could not apply — the two hashes disagree"
    finally:
        conn.close()


def test_unrecoverable_file_stops_counting_as_backlog(env):
    assert _run(env, "--apply").returncode == 0
    # File is gone: nothing to archive, so it must not pin the backlog metric.
    assert _sync_status(env["media_db"], "rec-gone") == "deleted"
    # But an undecodable file that still EXISTS is a real integrity problem and
    # must stay visible rather than being quietly written off.
    assert _sync_status(env["media_db"], "rec-corrupt") == "pending"


def test_refuses_a_file_ffprobe_cannot_decode(env):
    result = _run(env)
    assert "REFUSE" in result.stdout
    assert "bell_clip_corrupt.mp4" in result.stdout
    assert "moov atom not found" in result.stdout or "ffprobe rejected" in result.stdout
