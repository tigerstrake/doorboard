"""SQLite persistence for session state.

The session state is persisted in a single-row table so that:
- A kill -9 + restart resumes the live session.
- A kiosk browser reload can rejoin the current session state.

SQLite WAL mode is used for non-blocking reads from the WebSocket broadcast path.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from doorboard_contracts import SessionState


@dataclass(frozen=True)
class PersistedSession:
    """A session row as loaded from the database."""

    session_id: UUID
    state: SessionState
    trace_id: UUID
    person_id: str | None
    display_name: str | None
    profile_id: str | None
    started_at_monotonic_ms: int
    last_transition_monotonic_ms: int
    meta_json: str  # arbitrary JSON string for timer reconstruction


_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS session_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    session_id TEXT NOT NULL,
    state TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    person_id TEXT,
    display_name TEXT,
    profile_id TEXT,
    started_at_monotonic_ms INTEGER NOT NULL,
    last_transition_monotonic_ms INTEGER NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS media_outbox (
    event_id TEXT PRIMARY KEY,
    event_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_epoch REAL NOT NULL DEFAULT 0,
    created_epoch REAL NOT NULL,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_media_outbox_ready
ON media_outbox(next_attempt_epoch, created_epoch);

CREATE TABLE IF NOT EXISTS media_outbox_stats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    dropped_total INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO media_outbox_stats(id, dropped_total) VALUES (1, 0);

CREATE TABLE IF NOT EXISTS sync_outbox (
    event_id TEXT PRIMARY KEY,
    event_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_epoch REAL NOT NULL DEFAULT 0,
    created_epoch REAL NOT NULL,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_outbox_ready
ON sync_outbox(next_attempt_epoch, created_epoch);

CREATE TABLE IF NOT EXISTS sync_outbox_stats (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    dropped_total INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO sync_outbox_stats(id, dropped_total) VALUES (1, 0);
"""


@dataclass(frozen=True)
class MediaOutboxItem:
    event_id: str
    event: dict[str, Any]
    attempts: int


@dataclass(frozen=True)
class SyncOutboxItem:
    event_id: str
    event: dict[str, Any]
    attempts: int


class SessionStore:
    """Manages the SQLite-backed session persistence."""

    def __init__(
        self,
        db_path: str = ":memory:",
        *,
        media_outbox_max_rows: int = 4096,
        sync_outbox_max_rows: int = 4096,
    ) -> None:
        if media_outbox_max_rows < 1:
            raise ValueError("media_outbox_max_rows must be positive")
        if sync_outbox_max_rows < 1:
            raise ValueError("sync_outbox_max_rows must be positive")
        self._lock = threading.Lock()
        self._media_outbox_max_rows = media_outbox_max_rows
        self._sync_outbox_max_rows = sync_outbox_max_rows
        if db_path not in (":memory:", "") and not db_path.startswith("file:"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            db_path,
            isolation_level="DEFERRED",
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=FULL;")
        self._conn.executescript(_CREATE_TABLE)
        self._conn.commit()

    def load(self) -> PersistedSession | None:
        """Load the current session, or None if no session is persisted."""
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id, state, trace_id, person_id, display_name, profile_id, "
                "started_at_monotonic_ms, last_transition_monotonic_ms, meta_json "
                "FROM session_state WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        return PersistedSession(
            session_id=UUID(row[0]),
            state=SessionState(row[1]),
            trace_id=UUID(row[2]),
            person_id=row[3],
            display_name=row[4],
            profile_id=row[5],
            started_at_monotonic_ms=row[6],
            last_transition_monotonic_ms=row[7],
            meta_json=row[8],
        )

    def save(self, session: PersistedSession) -> None:
        """Upsert the session state (always row id=1)."""
        with self._lock:
            self._save_locked(session)
            self._conn.commit()

    def save_with_media_event(
        self,
        session: PersistedSession,
        event: dict[str, Any],
    ) -> int:
        """Atomically persist session state and enqueue its media projection."""
        with self._lock:
            self._save_locked(session)
            dropped = self._enqueue_media_event_locked(event)
            self._conn.commit()
            return dropped

    def save_with_delivery_events(
        self,
        session: PersistedSession,
        *,
        media_event: dict[str, Any],
        sync_events: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Atomically persist state and enqueue all remote projections."""
        with self._lock:
            self._save_locked(session)
            media_dropped = self._enqueue_media_event_locked(media_event)
            sync_dropped = sum(self._enqueue_sync_event_locked(event) for event in sync_events)
            self._conn.commit()
            return media_dropped, sync_dropped

    def _save_locked(self, session: PersistedSession) -> None:
        self._conn.execute(
            "INSERT INTO session_state "
            "(id, session_id, state, trace_id, person_id, display_name, profile_id, "
            "started_at_monotonic_ms, last_transition_monotonic_ms, meta_json) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "session_id=excluded.session_id, state=excluded.state, "
            "trace_id=excluded.trace_id, "
            "person_id=excluded.person_id, display_name=excluded.display_name, "
            "profile_id=excluded.profile_id, "
            "started_at_monotonic_ms=excluded.started_at_monotonic_ms, "
            "last_transition_monotonic_ms=excluded.last_transition_monotonic_ms, "
            "meta_json=excluded.meta_json",
            (
                str(session.session_id),
                session.state.value,
                str(session.trace_id),
                session.person_id,
                session.display_name,
                session.profile_id,
                session.started_at_monotonic_ms,
                session.last_transition_monotonic_ms,
                session.meta_json,
            ),
        )

    def clear(self) -> None:
        """Delete the persisted session (back to IDLE)."""
        with self._lock:
            self._conn.execute("DELETE FROM session_state WHERE id = 1")
            self._conn.commit()

    def clear_with_media_event(self, event: dict[str, Any]) -> int:
        """Atomically clear the active session and retain its final projection."""
        with self._lock:
            self._conn.execute("DELETE FROM session_state WHERE id = 1")
            dropped = self._enqueue_media_event_locked(event)
            self._conn.commit()
            return dropped

    def clear_with_delivery_events(
        self,
        *,
        media_event: dict[str, Any],
        sync_events: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Atomically clear state and enqueue final remote projections."""
        with self._lock:
            self._conn.execute("DELETE FROM session_state WHERE id = 1")
            media_dropped = self._enqueue_media_event_locked(media_event)
            sync_dropped = sum(self._enqueue_sync_event_locked(event) for event in sync_events)
            self._conn.commit()
            return media_dropped, sync_dropped

    def _enqueue_media_event_locked(self, event: dict[str, Any]) -> int:
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("media outbox event requires a string event_id")
        if self._conn.execute(
            "SELECT 1 FROM media_outbox WHERE event_id = ?",
            (event_id,),
        ).fetchone():
            return 0

        count = int(self._conn.execute("SELECT COUNT(*) FROM media_outbox").fetchone()[0])
        dropped = max(0, count - self._media_outbox_max_rows + 1)
        if dropped:
            self._conn.execute(
                "DELETE FROM media_outbox WHERE event_id IN "
                "(SELECT event_id FROM media_outbox ORDER BY rowid LIMIT ?)",
                (dropped,),
            )
            self._conn.execute(
                "UPDATE media_outbox_stats SET dropped_total = dropped_total + ? WHERE id = 1",
                (dropped,),
            )

        self._conn.execute(
            "INSERT INTO media_outbox "
            "(event_id, event_json, attempts, next_attempt_epoch, created_epoch) "
            "VALUES (?, ?, 0, 0, strftime('%s','now'))",
            (event_id, json.dumps(event, separators=(",", ":"), sort_keys=True)),
        )
        return dropped

    def next_media_event(self, now_epoch: float) -> MediaOutboxItem | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT event_id, event_json, attempts, next_attempt_epoch FROM media_outbox "
                "ORDER BY rowid LIMIT 1",
            ).fetchone()
        if row is None or float(row[3]) > now_epoch:
            return None
        event = json.loads(row[1])
        if not isinstance(event, dict):
            raise ValueError("media outbox event_json is not an object")
        return MediaOutboxItem(event_id=row[0], event=event, attempts=row[2])

    def complete_media_event(self, event_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM media_outbox WHERE event_id = ?", (event_id,))
            self._conn.commit()

    def retry_media_event(
        self,
        event_id: str,
        *,
        attempts: int,
        next_attempt_epoch: float,
        last_error: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE media_outbox SET attempts = ?, next_attempt_epoch = ?, last_error = ? "
                "WHERE event_id = ?",
                (attempts, next_attempt_epoch, last_error[:500], event_id),
            )
            self._conn.commit()

    def media_outbox_depth(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM media_outbox").fetchone()[0])

    def media_outbox_dropped_total(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT dropped_total FROM media_outbox_stats WHERE id = 1"
            ).fetchone()
        return int(row[0]) if row else 0

    def enqueue_sync_event(self, event: dict[str, Any]) -> int:
        """Durably enqueue one non-session event for door-sync delivery."""
        with self._lock:
            dropped = self._enqueue_sync_event_locked(event)
            self._conn.commit()
            return dropped

    def _enqueue_sync_event_locked(self, event: dict[str, Any]) -> int:
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("sync outbox event requires a string event_id")
        if self._conn.execute(
            "SELECT 1 FROM sync_outbox WHERE event_id = ?",
            (event_id,),
        ).fetchone():
            return 0

        count = int(self._conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0])
        dropped = max(0, count - self._sync_outbox_max_rows + 1)
        if dropped:
            self._conn.execute(
                "DELETE FROM sync_outbox WHERE event_id IN "
                "(SELECT event_id FROM sync_outbox ORDER BY rowid LIMIT ?)",
                (dropped,),
            )
            self._conn.execute(
                "UPDATE sync_outbox_stats SET dropped_total = dropped_total + ? WHERE id = 1",
                (dropped,),
            )

        self._conn.execute(
            "INSERT INTO sync_outbox "
            "(event_id, event_json, attempts, next_attempt_epoch, created_epoch) "
            "VALUES (?, ?, 0, 0, strftime('%s','now'))",
            (event_id, json.dumps(event, separators=(",", ":"), sort_keys=True)),
        )
        return dropped

    def next_sync_event(self, now_epoch: float) -> SyncOutboxItem | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT event_id, event_json, attempts, next_attempt_epoch FROM sync_outbox "
                "ORDER BY rowid LIMIT 1",
            ).fetchone()
        if row is None or float(row[3]) > now_epoch:
            return None
        event = json.loads(row[1])
        if not isinstance(event, dict):
            raise ValueError("sync outbox event_json is not an object")
        return SyncOutboxItem(event_id=row[0], event=event, attempts=row[2])

    def complete_sync_event(self, event_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sync_outbox WHERE event_id = ?", (event_id,))
            self._conn.commit()

    def retry_sync_event(
        self,
        event_id: str,
        *,
        attempts: int,
        next_attempt_epoch: float,
        last_error: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sync_outbox SET attempts = ?, next_attempt_epoch = ?, last_error = ? "
                "WHERE event_id = ?",
                (attempts, next_attempt_epoch, last_error[:500], event_id),
            )
            self._conn.commit()

    def sync_outbox_depth(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0])

    def sync_outbox_dropped_total(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT dropped_total FROM sync_outbox_stats WHERE id = 1"
            ).fetchone()
        return int(row[0]) if row else 0

    def save_meta(self, meta: dict[str, object]) -> None:
        """Update only the meta_json column for timer reconstruction."""
        with self._lock:
            self._conn.execute(
                "UPDATE session_state SET meta_json = ? WHERE id = 1",
                (json.dumps(meta, default=str),),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
