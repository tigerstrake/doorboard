"""Durable-queue unit tests (SQLite WAL crash-consistency primitives)."""

from __future__ import annotations

from pathlib import Path

from door_sync.queue import NewItem, QueueItem, UploadQueue


def _item(item_id: str, **kw: object) -> NewItem:
    base: dict[str, object] = {
        "item_id": item_id,
        "kind": "event",
        "target": "nuc",
        "dest_key": item_id,
        "trace_id": "00000000-0000-7000-8000-000000000000",
        "payload": "{}",
    }
    base.update(kw)
    return NewItem(**base)  # type: ignore[arg-type]


def _get(q: UploadQueue, item_id: str) -> QueueItem:
    item = q.get(item_id)
    assert item is not None
    return item


def test_enqueue_is_idempotent_by_item_id(tmp_path: Path) -> None:
    q = UploadQueue(tmp_path / "q.sqlite")
    try:
        assert q.enqueue(_item("a")) is True
        assert q.enqueue(_item("a")) is False  # duplicate ignored
        assert len(q.list_items()) == 1
    finally:
        q.close()


def test_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "q.sqlite"
    q = UploadQueue(db)
    q.enqueue(_item("a"))
    q.close()
    # Reopen from disk — the item is still there (WAL durability).
    q2 = UploadQueue(db)
    try:
        assert _get(q2, "a").status == "pending"
    finally:
        q2.close()


def test_due_items_respects_backoff(tmp_path: Path) -> None:
    q = UploadQueue(tmp_path / "q.sqlite")
    try:
        q.enqueue(_item("a"))
        # Fail once, pushing next_attempt_at into the future.
        q.record_failure(
            "a",
            permanent=False,
            next_attempt_at=1e12,
            error_class="TransientError",
            message="down",
            max_permanent_attempts=5,
        )
        assert q.due_items(now_epoch=0.0) == []
        assert len(q.due_items(now_epoch=1e12 + 1)) == 1
    finally:
        q.close()


def test_transient_never_dead_letters(tmp_path: Path) -> None:
    q = UploadQueue(tmp_path / "q.sqlite")
    try:
        q.enqueue(_item("a"))
        for _ in range(50):
            status = q.record_failure(
                "a",
                permanent=False,
                next_attempt_at=0.0,
                error_class="TransientError",
                message="down",
                max_permanent_attempts=3,
            )
            assert status == "pending"
        assert _get(q, "a").permanent_failures == 0
    finally:
        q.close()


def test_permanent_dead_letters_at_cap(tmp_path: Path) -> None:
    q = UploadQueue(tmp_path / "q.sqlite")
    try:
        q.enqueue(_item("a"))
        statuses = [
            q.record_failure(
                "a",
                permanent=True,
                next_attempt_at=0.0,
                error_class="PermanentError",
                message="bad",
                max_permanent_attempts=3,
            )
            for _ in range(3)
        ]
        assert statuses == ["pending", "pending", "dead_letter"]
        assert _get(q, "a").status == "dead_letter"
        # Dead-lettered items are no longer due for processing.
        assert q.due_items(now_epoch=1e12) == []
    finally:
        q.close()


def test_mark_completed_only_from_pending(tmp_path: Path) -> None:
    q = UploadQueue(tmp_path / "q.sqlite")
    try:
        q.enqueue(_item("a"))
        q.mark_completed("a", verified_sha256="x", licensed=True)
        assert _get(q, "a").status == "completed"
        # A second completion (e.g. duplicate delivery) is a no-op, not a crash.
        q.mark_completed("a", verified_sha256="y", licensed=True)
        assert _get(q, "a").verified_sha256 == "x"
    finally:
        q.close()


def test_prune_completed_keeps_unlicensed_clip(tmp_path: Path) -> None:
    q = UploadQueue(tmp_path / "q.sqlite")
    try:
        q.enqueue(
            _item(
                "clip1",
                kind="clip",
                target="nas",
                dest_key="recordings/c.mp4",
                recording_id="r1",
                local_path="recordings/c.mp4",
            )
        )
        # Completed but not yet licensed for deletion — must NOT be pruned.
        q.mark_completed("clip1", verified_sha256="x", licensed=False)
        assert q.prune_completed(older_than_iso="9999-01-01T00:00:00+00:00") == 0
        q.mark_licensed("clip1")
        assert q.prune_completed(older_than_iso="9999-01-01T00:00:00+00:00") == 1
    finally:
        q.close()


def test_stats(tmp_path: Path) -> None:
    q = UploadQueue(tmp_path / "q.sqlite")
    try:
        q.enqueue(_item("a", target="nuc"))
        q.enqueue(
            _item(
                "b",
                kind="clip",
                target="nas",
                dest_key="recordings/x",
                recording_id="r",
                local_path="recordings/x",
            )
        )
        q.record_failure(
            "b",
            permanent=True,
            next_attempt_at=0.0,
            error_class="PermanentError",
            message="x",
            max_permanent_attempts=1,
        )
        stats = q.stats(now_epoch=1e12)
        assert stats.pending == 1
        assert stats.dead_letter == 1
        assert stats.per_target["nas"]["dead_letter"] == 1
    finally:
        q.close()
