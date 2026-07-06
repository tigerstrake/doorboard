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


def test_list_recordings_pagination_does_not_drop_duplicate_timestamps(db: RecordingDB):
    """A cursor keyed on started_at_utc alone would silently skip rows that
    share a timestamp with the page boundary. Force three rows onto the
    exact same started_at_utc (indistinguishable by the column alone) and
    page through with limit=1, asserting every row surfaces exactly once.
    """
    ids = [uuid4() for _ in range(3)]
    for rid in ids:
        db.insert_started(
            recording_id=rid,
            session_id=uuid4(),
            kind="bell_clip",
            stream="visitor",
            started_mono_ms=1000,
        )

    shared_timestamp = "2026-01-01T00:00:00+00:00"
    with db._lock:
        db._conn.execute(
            "UPDATE recordings SET started_at_utc = ?",
            (shared_timestamp,),
        )
        db._conn.commit()

    seen: list[str] = []
    cursor = None
    for _ in range(len(ids) + 1):
        rows, cursor = db.list_recordings(limit=1, cursor=cursor)
        if not rows:
            break
        seen.extend(row.recording_id for row in rows)
        if cursor is None:
            break

    assert sorted(seen) == sorted(str(rid) for rid in ids)
    assert len(seen) == len(set(seen))
