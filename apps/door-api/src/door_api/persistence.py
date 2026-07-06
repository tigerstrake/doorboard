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
"""


class SessionStore:
    """Manages the SQLite-backed session persistence."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path,
            isolation_level="DEFERRED",
            check_same_thread=False,
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute(_CREATE_TABLE)
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
            self._conn.commit()

    def clear(self) -> None:
        """Delete the persisted session (back to IDLE)."""
        with self._lock:
            self._conn.execute("DELETE FROM session_state WHERE id = 1")
            self._conn.commit()

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
