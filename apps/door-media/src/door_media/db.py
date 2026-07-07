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

import base64
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
    consent_metadata_path TEXT,
    sync_status    TEXT NOT NULL DEFAULT 'pending',
    synced_sha256  TEXT,
    deleted_at_utc TEXT
);

CREATE INDEX IF NOT EXISTS idx_recordings_sync_status ON recordings(sync_status);
CREATE INDEX IF NOT EXISTS idx_recordings_started_at  ON recordings(started_at_utc);
CREATE INDEX IF NOT EXISTS idx_recordings_session_kind ON recordings(session_id, kind);
"""

_ROW_COLUMNS = (
    "recording_id",
    "session_id",
    "kind",
    "stream",
    "started_at_utc",
    "started_mono_ms",
    "finalized_at_utc",
    "path",
    "duration_s",
    "size_bytes",
    "sha256",
    "consent_context",
    "thumbnail_path",
    "consent_metadata_path",
    "sync_status",
    "synced_sha256",
    "deleted_at_utc",
)
_ROW_SELECT = ", ".join(_ROW_COLUMNS)


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
    consent_metadata_path: str | None
    sync_status: str
    synced_sha256: str | None
    deleted_at_utc: str | None


_CURSOR_SEP = "\x1f"  # unit separator — never appears in a timestamp or UUID


def _encode_recording_cursor(started_at_utc: str, recording_id: str) -> str:
    raw = f"{started_at_utc}{_CURSOR_SEP}{recording_id}"
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


def _decode_recording_cursor(cursor: str) -> tuple[str, str] | None:
    try:
        decoded = base64.b64decode(cursor.encode("utf-8")).decode("utf-8")
        started_at_utc, recording_id = decoded.split(_CURSOR_SEP, 1)
        return started_at_utc, recording_id
    except Exception:
        return None


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
        self._ensure_column("consent_metadata_path", "TEXT")
        self._conn.commit()
        logger.info("recording_db_opened", extra={"path": str(db_path)})

    def _ensure_column(self, name: str, decl: str) -> None:
        existing = {
            row[1] for row in self._conn.execute("PRAGMA table_info(recordings)").fetchall()
        }
        if name not in existing:
            self._conn.execute(f"ALTER TABLE recordings ADD COLUMN {name} {decl}")

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

    def update_consent_metadata(self, *, recording_id: UUID, metadata_path: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE recordings SET consent_metadata_path=? WHERE recording_id=?",
                (metadata_path, str(recording_id)),
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

    def delete_unfinalized(self, *, recording_id: UUID) -> bool:
        """Remove a started row that never produced a durable clip."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM recordings WHERE recording_id=? AND path IS NULL",
                (str(recording_id),),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def rows_for_session(self, *, session_id: UUID, kind: str | None = None) -> list[RecordingRow]:
        query = f"SELECT {_ROW_SELECT} FROM recordings WHERE session_id=?"
        params: list[str] = [str(session_id)]
        if kind is not None:
            query += " AND kind=?"
            params.append(kind)
        query += " ORDER BY started_at_utc"
        with self._lock:
            cur = self._conn.execute(query, params)
            rows = cur.fetchall()
        return [RecordingRow(*r) for r in rows]

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
                f"SELECT {_ROW_SELECT} FROM recordings WHERE recording_id=?",
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
                f"""SELECT {_ROW_SELECT} FROM recordings
                   WHERE sync_status != 'deleted'
                   ORDER BY started_at_utc""",
            )
            rows = cur.fetchall()
        return [RecordingRow(*r) for r in rows]

    def list_recordings(
        self,
        *,
        kind: str | None = None,
        sync_status: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[RecordingRow], str | None]:
        """List recordings with filtering and cursor-based pagination.

        The cursor encodes ``(started_at_utc, recording_id)`` rather than
        ``started_at_utc`` alone: two recordings can share a timestamp (the
        column has microsecond resolution but no uniqueness guarantee), and
        a cursor keyed on a non-unique column silently drops every row that
        shares the boundary value with a strict ``>`` comparison. Pairing it
        with the primary key gives a stable total order.
        """
        with self._lock:
            query = f"SELECT {_ROW_SELECT} FROM recordings WHERE sync_status != 'deleted'"
            params: list[object] = []

            if kind:
                query += " AND kind = ?"
                params.append(kind)

            if sync_status:
                query += " AND sync_status = ?"
                params.append(sync_status)

            if cursor:
                decoded_cursor = _decode_recording_cursor(cursor)
                if decoded_cursor is None:
                    logger.warning("invalid_cursor_ignored", extra={"cursor": cursor})
                else:
                    cursor_started_at, cursor_recording_id = decoded_cursor
                    query += " AND (started_at_utc, recording_id) > (?, ?)"
                    params.extend([cursor_started_at, cursor_recording_id])

            query += " ORDER BY started_at_utc ASC, recording_id ASC"

            if limit is not None:
                query += " LIMIT ?"
                params.append(limit + 1)

            cur = self._conn.execute(query, params)
            rows = cur.fetchall()

        recording_rows = [RecordingRow(*r) for r in rows]

        next_cursor = None
        if limit is not None and len(recording_rows) > limit:
            last_item = recording_rows[limit - 1]
            next_cursor = _encode_recording_cursor(last_item.started_at_utc, last_item.recording_id)
            recording_rows = recording_rows[:limit]

        return recording_rows, next_cursor

    def list_finalized_pending_sync(self) -> list[RecordingRow]:
        """Recordings finalized but not yet synced — for the sync queue depth."""
        with self._lock:
            cur = self._conn.execute(
                f"""SELECT {_ROW_SELECT} FROM recordings
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
