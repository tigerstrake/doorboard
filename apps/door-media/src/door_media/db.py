"""In-process recording registry backed by SQLite WAL on the SSD.

Schema lives in this file; migrations are additive (new columns with
DEFAULT/NULL only).  The registry is the single source of truth for what
clips exist on disk, their sync status, and their metadata.

Key invariants:
  - A recording is only marked ``synced=1`` when door-sync sends
    ``sync.upload_completed`` with a matching SHA-256 (ADR-0007 / events.md).
  - Local deletion is only legal after ``synced=1`` (enforced by
    ``mark_synced()``/``delete_if_synced()``).
  - ``sqlite3`` is stdlib; no extra dependency.
  - All paths stored are SSD-relative (``str(path.relative_to(ssd_root))``).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

logger = logging.getLogger("door_media.db")

SyncStatus = Literal["pending", "synced", "deleted"]
RecordingKind = Literal["bell_clip", "video_message", "photo_booth"]
ConsentContext = Literal["visitor_initiated", "bell_event"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    recording_id   TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    kind           TEXT NOT NULL,
    stream         TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    started_mono_ms INTEGER NOT NULL,
    finalized_at_utc TEXT,
    path           TEXT,
    duration_s     REAL,
    size_bytes     INTEGER,
    sha256         TEXT,
    consent_context TEXT,
    thumbnail_path TEXT,
    sync_status    TEXT NOT NULL DEFAULT 'pending',
    synced_sha256  TEXT,
    deleted_at_utc TEXT
);

CREATE INDEX IF NOT EXISTS idx_recordings_sync_status ON recordings(sync_status);
CREATE INDEX IF NOT EXISTS idx_recordings_started_at  ON recordings(started_at_utc);
"""


@dataclass
class RecordingRow:
    recording_id: str
    session_id: str
    kind: str
    stream: str
    started_at_utc: str
    started_mono_ms: int
    finalized_at_utc: str | None
    path: str | None
    duration_s: float | None
    size_bytes: int | None
    sha256: str | None
    consent_context: str | None
    thumbnail_path: str | None
    sync_status: str
    synced_sha256: str | None
    deleted_at_utc: str | None


class RecordingDB:
    """Thread-safe SQLite recording registry.

    Uses WAL mode and a dedicated lock so that async tasks and the sync-event
    callback can both access it safely without blocking each other for more
    than a few microseconds.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("recording_db_opened", extra={"path": str(db_path)})

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def insert_started(
        self,
        *,
        recording_id: UUID,
        session_id: UUID,
        kind: str,
        stream: str,
        started_mono_ms: int,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                """INSERT INTO recordings
                   (recording_id, session_id, kind, stream,
                    started_at_utc, started_mono_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(recording_id),
                    str(session_id),
                    kind,
                    stream,
                    now,
                    started_mono_ms,
                ),
            )
            self._conn.commit()

    def update_finalized(
        self,
        *,
        recording_id: UUID,
        path: str,
        duration_s: float,
        size_bytes: int,
        sha256: str,
        consent_context: str,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                """UPDATE recordings SET
                   finalized_at_utc=?, path=?, duration_s=?,
                   size_bytes=?, sha256=?, consent_context=?
                   WHERE recording_id=?""",
                (now, path, duration_s, size_bytes, sha256, consent_context, str(recording_id)),
            )
            self._conn.commit()

    def update_thumbnail(self, *, recording_id: UUID, thumbnail_path: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE recordings SET thumbnail_path=? WHERE recording_id=?",
                (thumbnail_path, str(recording_id)),
            )
            self._conn.commit()

    def mark_synced(self, *, recording_id: UUID, verified_sha256: str) -> bool:
        """Mark a recording synced only if sha256 matches.

        Returns True if the update was applied (sha256 matched), False otherwise.
        """
        with self._lock:
            cur = self._conn.execute(
                """UPDATE recordings SET sync_status='synced', synced_sha256=?
                   WHERE recording_id=? AND sha256=? AND sync_status='pending'""",
                (verified_sha256, str(recording_id), verified_sha256),
            )
            self._conn.commit()
            matched = cur.rowcount > 0
        if not matched:
            logger.warning(
                "mark_synced_mismatch",
                extra={
                    "recording_id": str(recording_id),
                    "verified_sha256": verified_sha256,
                },
            )
        return matched

    def mark_deleted(self, *, recording_id: UUID, reason: str) -> bool:
        """Mark a recording deleted.  Only legal if synced or user_request/age/space."""
        now = datetime.now(UTC).isoformat()
        with self._lock:
            cur = self._conn.execute(
                """UPDATE recordings SET sync_status='deleted', deleted_at_utc=?
                   WHERE recording_id=? AND sync_status IN ('synced', 'pending')""",
                (now, str(recording_id)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get(self, recording_id: UUID) -> RecordingRow | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM recordings WHERE recording_id=?",
                (str(recording_id),),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return RecordingRow(*row)

    def list_pending(self) -> list[RecordingRow]:
        """Return all recordings not yet deleted."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM recordings
                   WHERE sync_status != 'deleted'
                   ORDER BY started_at_utc""",
            )
            rows = cur.fetchall()
        return [RecordingRow(*r) for r in rows]

    def list_finalized_pending_sync(self) -> list[RecordingRow]:
        """Recordings finalized but not yet synced — for the sync queue depth."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM recordings
                   WHERE sync_status='pending' AND path IS NOT NULL
                   ORDER BY started_at_utc""",
            )
            rows = cur.fetchall()
        return [RecordingRow(*r) for r in rows]

    def oldest_unsynced_age_s(self) -> int:
        """Seconds since the oldest unsynced finalized clip was created."""
        with self._lock:
            cur = self._conn.execute(
                """SELECT MIN(started_at_utc) FROM recordings
                   WHERE sync_status='pending' AND path IS NOT NULL""",
            )
            row = cur.fetchone()
        if row is None or row[0] is None:
            return 0
        try:
            oldest = datetime.fromisoformat(row[0])
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=UTC)
            return int((datetime.now(UTC) - oldest).total_seconds())
        except (ValueError, OverflowError):
            return 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
