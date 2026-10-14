from uuid import uuid4

import pytest
from door_media.db import RecordingDB


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    database = RecordingDB(db_path)
    yield database
    database.close()


def test_db_lifecycle(db: RecordingDB):
    rid = uuid4()
    sid = uuid4()

    # 1. Insert started
    db.insert_started(
        recording_id=rid,
        session_id=sid,
        kind="bell_clip",
        stream="visitor",
        started_mono_ms=1000,
    )

    row = db.get(rid)
    assert row is not None
    assert row.recording_id == str(rid)
    assert row.sync_status == "pending"
    assert row.path is None

    # 2. Update finalized
    db.update_finalized(
        recording_id=rid,
        path="foo.mp4",
        duration_s=1.5,
        size_bytes=1024,
        sha256="abc",
        consent_context="bell_event",
    )

    row = db.get(rid)
    assert row is not None
    assert row.path == "foo.mp4"

    # 3. List pending sync
    pending_sync = db.list_finalized_pending_sync()
    assert len(pending_sync) == 1
    assert pending_sync[0].recording_id == str(rid)

    # 4. Mark synced (wrong sha = fail)
    matched = db.mark_synced(recording_id=rid, verified_sha256="wrong")
    assert not matched
    row = db.get(rid)
    assert row is not None
    assert row.sync_status == "pending"

    # 5. Mark synced (correct sha)
    matched = db.mark_synced(recording_id=rid, verified_sha256="abc")
    assert matched
    row = db.get(rid)
    assert row is not None
    assert row.sync_status == "synced"

    # 6. Mark deleted
    deleted = db.mark_deleted(recording_id=rid, reason="user_request")
    assert deleted
    row = db.get(rid)
    assert row is not None
    assert row.sync_status == "deleted"


def test_list_recordings_pagination_tie_breaker(db: RecordingDB):
    timestamp = "2026-07-06T21:00:00.000000+00:00"
    ids = [uuid4() for _ in range(3)]
    ids_sorted = sorted(ids, key=lambda u: str(u))

    for rid in ids:
        db._conn.execute(
            """INSERT INTO recordings
               (recording_id, session_id, kind, stream, started_at_utc, started_mono_ms, sync_status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
            (str(rid), str(uuid4()), "bell_clip", "visitor", timestamp, 1000),
        )
    db._conn.commit()

    # First page
    rows, cursor1 = db.list_recordings(limit=1)
    assert len(rows) == 1
    assert rows[0].recording_id == str(ids_sorted[0])
    assert cursor1 is not None

    # Second page
    rows, cursor2 = db.list_recordings(limit=1, cursor=cursor1)
    assert len(rows) == 1
    assert rows[0].recording_id == str(ids_sorted[1])
    assert cursor2 is not None

    # Third page
    rows, cursor3 = db.list_recordings(limit=1, cursor=cursor2)
    assert len(rows) == 1
    assert rows[0].recording_id == str(ids_sorted[2])
    assert cursor3 is None

    # Verify that querying with a cursor for the last element returns no rows
    import base64

    last_cursor_val = f"{rows[0].started_at_utc}|{rows[0].recording_id}"
    last_cursor = base64.b64encode(last_cursor_val.encode("utf-8")).decode("utf-8")
    rows, cursor4 = db.list_recordings(limit=1, cursor=last_cursor)
    assert len(rows) == 0
    assert cursor4 is None
