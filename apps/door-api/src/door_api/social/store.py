"""SQLite persistence for guestbook, polls, check-ins, and the moderation log.

Same WAL-mode pattern as ``door_api.persistence.SessionStore``: a single
long-lived connection per process. Unlike the session store, this one is
reached from synchronous FastAPI route handlers, which Starlette may run on
a worker thread different from the one that created the connection — so it
is opened with ``check_same_thread=False`` and every method serializes
through a lock (a bare ``sqlite3.Connection`` is not safe for concurrent use
from multiple threads even with that flag).
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

GuestbookStatus = Literal["pending", "approved", "deleted"]
PollStatus = Literal["open", "closed"]

_CREATE_TABLES = """\
CREATE TABLE IF NOT EXISTS guestbook_entries (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    author_label TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    ip_hash TEXT,
    session_key_hash TEXT,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS polls (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS poll_options (
    id TEXT PRIMARY KEY,
    poll_id TEXT NOT NULL REFERENCES polls(id),
    text TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS poll_votes (
    poll_id TEXT NOT NULL,
    session_token TEXT NOT NULL,
    option_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (poll_id, session_token)
);

CREATE TABLE IF NOT EXISTS checkins (
    id TEXT PRIMARY KEY,
    person_id TEXT,
    label TEXT,
    photo_recording_id TEXT,
    session_key_hash TEXT,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS moderation_log (
    id TEXT PRIMARY KEY,
    target_kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_guestbook_status ON guestbook_entries(status, created_at);
CREATE INDEX IF NOT EXISTS idx_checkins_person ON checkins(person_id);
CREATE INDEX IF NOT EXISTS idx_poll_options_poll ON poll_options(poll_id);
"""


@dataclass(frozen=True)
class GuestbookEntry:
    id: str
    text: str
    author_label: str | None
    status: GuestbookStatus
    created_at: str
    deleted_at: str | None


@dataclass(frozen=True)
class PollOption:
    id: str
    poll_id: str
    text: str
    sort_order: int


@dataclass(frozen=True)
class Poll:
    id: str
    question: str
    status: PollStatus
    created_at: str
    closed_at: str | None
    options: tuple[PollOption, ...]


@dataclass(frozen=True)
class Checkin:
    id: str
    person_id: str | None
    label: str | None
    photo_recording_id: str | None
    created_at: str
    deleted_at: str | None


@dataclass(frozen=True)
class CheckinStats:
    """Aggregate, count-only visitor stats (no images, no biometric data).

    Computed over non-deleted check-ins. ``unique_visitors`` groups enrolled
    people by distinct ``person_id`` and treats every anonymous/guest check-in
    as its own distinct visit (so a busy year of one-off guests still reads as
    many visitors, which is what the "who's stopped by" tile wants to celebrate).
    """

    total_checkins: int
    checkins_this_year: int
    distinct_persons: int
    guest_count: int
    first_checkin_at: str | None
    most_recent_checkin_at: str | None

    @property
    def unique_visitors(self) -> int:
        return self.distinct_persons + self.guest_count


class SocialStore:
    """Manages the SQLite-backed guestbook/poll/checkin persistence."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        if db_path not in (":memory:", "") and not db_path.startswith("file:"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, isolation_level="DEFERRED", check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.executescript(_CREATE_TABLES)
            self._ensure_column("guestbook_entries", "session_key_hash", "TEXT")
            self._ensure_column("checkins", "session_key_hash", "TEXT")
            # Additive column (ADR-0013): upgrade existing DBs so a check-in can
            # carry an optional reference to a visitor-captured photo.
            self._ensure_column("checkins", "photo_recording_id", "TEXT")
            # Created after the column migration so it also applies to DBs that
            # predate photo_recording_id — the collage joins on this reference.
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkins_photo ON checkins(photo_recording_id)"
            )
            self._conn.commit()

    def _ensure_column(self, table: str, column: str, sql_type: str) -> None:
        columns = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    # ------------------------------------------------------------------
    # Guestbook
    # ------------------------------------------------------------------

    def insert_guestbook_entry(
        self,
        *,
        entry_id: str,
        text: str,
        author_label: str | None,
        status: GuestbookStatus,
        ip_hash: str,
        session_key_hash: str,
        created_at: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO guestbook_entries "
                "(id, text, author_label, status, ip_hash, session_key_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entry_id, text, author_label, status, ip_hash, session_key_hash, created_at),
            )
            self._conn.commit()

    def get_guestbook_entry(self, entry_id: str) -> GuestbookEntry | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, text, author_label, status, created_at, deleted_at "
                "FROM guestbook_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()
        return self._row_to_guestbook_entry(row) if row else None

    def list_guestbook_entries(
        self, *, status: GuestbookStatus, limit: int, cursor_created_at: str | None
    ) -> list[GuestbookEntry]:
        with self._lock:
            if cursor_created_at is None:
                rows = self._conn.execute(
                    "SELECT id, text, author_label, status, created_at, deleted_at "
                    "FROM guestbook_entries WHERE status = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, text, author_label, status, created_at, deleted_at "
                    "FROM guestbook_entries WHERE status = ? AND created_at < ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (status, cursor_created_at, limit),
                ).fetchall()
        return [self._row_to_guestbook_entry(row) for row in rows]

    def approve_guestbook_entry(self, entry_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE guestbook_entries SET status = 'approved' "
                "WHERE id = ? AND status = 'pending'",
                (entry_id,),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def soft_delete_guestbook_entry(
        self,
        entry_id: str,
        *,
        deleted_at: str,
        session_key_hash: str | None = None,
    ) -> bool:
        with self._lock:
            query = (
                "UPDATE guestbook_entries SET status = 'deleted', deleted_at = ? "
                "WHERE id = ? AND status != 'deleted'"
            )
            values: tuple[str, ...] = (deleted_at, entry_id)
            if session_key_hash is not None:
                query += " AND session_key_hash = ?"
                values += (session_key_hash,)
            cur = self._conn.execute(query, values)
            self._conn.commit()
            return cur.rowcount > 0

    @staticmethod
    def _row_to_guestbook_entry(row: tuple) -> GuestbookEntry:
        return GuestbookEntry(
            id=row[0],
            text=row[1],
            author_label=row[2],
            status=row[3],
            created_at=row[4],
            deleted_at=row[5],
        )

    # ------------------------------------------------------------------
    # Polls
    # ------------------------------------------------------------------

    def insert_poll(
        self,
        *,
        poll_id: str,
        question: str,
        options: list[tuple[str, str]],  # (option_id, text)
        created_at: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO polls (id, question, status, created_at) VALUES (?, ?, 'open', ?)",
                (poll_id, question, created_at),
            )
            for idx, (option_id, text) in enumerate(options):
                self._conn.execute(
                    "INSERT INTO poll_options (id, poll_id, text, sort_order) VALUES (?, ?, ?, ?)",
                    (option_id, poll_id, text, idx),
                )
            self._conn.commit()

    def get_poll(self, poll_id: str) -> Poll | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, question, status, created_at, closed_at FROM polls WHERE id = ?",
                (poll_id,),
            ).fetchone()
            if row is None:
                return None
            return self._hydrate_poll_locked(row)

    def get_current_poll(self) -> Poll | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, question, status, created_at, closed_at FROM polls "
                "WHERE status = 'open' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            return self._hydrate_poll_locked(row)

    def list_polls(self, *, limit: int) -> list[Poll]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, question, status, created_at, closed_at FROM polls "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._hydrate_poll_locked(row) for row in rows]

    def _hydrate_poll_locked(self, row: tuple) -> Poll:
        """Build a Poll from a row. Caller must already hold ``self._lock``."""
        option_rows = self._conn.execute(
            "SELECT id, poll_id, text, sort_order FROM poll_options "
            "WHERE poll_id = ? ORDER BY sort_order ASC",
            (row[0],),
        ).fetchall()
        options = tuple(
            PollOption(id=o[0], poll_id=o[1], text=o[2], sort_order=o[3]) for o in option_rows
        )
        return Poll(
            id=row[0],
            question=row[1],
            status=row[2],
            created_at=row[3],
            closed_at=row[4],
            options=options,
        )

    def close_poll(self, poll_id: str, *, closed_at: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE polls SET status = 'closed', closed_at = ? "
                "WHERE id = ? AND status = 'open'",
                (closed_at, poll_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def option_belongs_to_poll(self, *, poll_id: str, option_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM poll_options WHERE id = ? AND poll_id = ?",
                (option_id, poll_id),
            ).fetchone()
        return row is not None

    def has_voted(self, *, poll_id: str, session_token: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM poll_votes WHERE poll_id = ? AND session_token = ?",
                (poll_id, session_token),
            ).fetchone()
        return row is not None

    def insert_vote(
        self, *, poll_id: str, session_token: str, option_id: str, created_at: str
    ) -> bool:
        """Insert a vote. Returns False if this (poll_id, session_token) already voted."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO poll_votes (poll_id, session_token, option_id, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (poll_id, session_token, option_id, created_at),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def poll_results(self, poll_id: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT option_id, COUNT(*) FROM poll_votes WHERE poll_id = ? GROUP BY option_id",
                (poll_id,),
            ).fetchall()
        return dict(rows)

    # ------------------------------------------------------------------
    # Check-ins
    # ------------------------------------------------------------------

    def insert_checkin(
        self,
        *,
        checkin_id: str,
        person_id: str | None,
        label: str | None,
        photo_recording_id: str | None,
        session_key_hash: str,
        created_at: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO checkins "
                "(id, person_id, label, photo_recording_id, session_key_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (checkin_id, person_id, label, photo_recording_id, session_key_hash, created_at),
            )
            self._conn.commit()

    def get_checkin(self, checkin_id: str) -> Checkin | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, person_id, label, photo_recording_id, created_at, deleted_at "
                "FROM checkins WHERE id = ?",
                (checkin_id,),
            ).fetchone()
        return self._row_to_checkin(row) if row else None

    def list_checkins(self, *, limit: int, cursor_created_at: str | None) -> list[Checkin]:
        with self._lock:
            if cursor_created_at is None:
                rows = self._conn.execute(
                    "SELECT id, person_id, label, photo_recording_id, created_at, deleted_at "
                    "FROM checkins WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, person_id, label, photo_recording_id, created_at, deleted_at "
                    "FROM checkins WHERE deleted_at IS NULL AND created_at < ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (cursor_created_at, limit),
                ).fetchall()
        return [self._row_to_checkin(row) for row in rows]

    def soft_delete_checkin(
        self,
        checkin_id: str,
        *,
        deleted_at: str,
        session_key_hash: str | None = None,
    ) -> bool:
        with self._lock:
            query = "UPDATE checkins SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL"
            values: tuple[str, ...] = (deleted_at, checkin_id)
            if session_key_hash is not None:
                query += " AND session_key_hash = ?"
                values += (session_key_hash,)
            cur = self._conn.execute(query, values)
            self._conn.commit()
            return cur.rowcount > 0

    def latest_label_for_person(self, person_id: str) -> str | None:
        """Most recent freeform label a person used at check-in, for display."""
        with self._lock:
            row = self._conn.execute(
                "SELECT label FROM checkins WHERE person_id = ? AND deleted_at IS NULL "
                "AND label IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                (person_id,),
            ).fetchone()
        return row[0] if row else None

    def most_frequent_checkin_person(self) -> tuple[str, int] | None:
        """Return (person_id, count) for the most-checked-in enrolled person.

        Anonymous check-ins (person_id IS NULL) never count toward this stat.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT person_id, COUNT(*) as n FROM checkins "
                "WHERE deleted_at IS NULL AND person_id IS NOT NULL "
                "GROUP BY person_id ORDER BY n DESC, person_id ASC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return (row[0], row[1])

    def aggregate_checkin_stats(self, *, year_start: str) -> CheckinStats:
        """Count-only aggregate over non-deleted check-ins for the visitor tile.

        ``year_start`` is an ISO timestamp (e.g. ``2026-01-01T00:00:00Z``); ISO
        strings sort lexically so a range comparison on ``created_at`` is exact
        and index-friendly. No images or biometric data are touched here.
        """
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM checkins WHERE deleted_at IS NULL"
            ).fetchone()[0]
            this_year = self._conn.execute(
                "SELECT COUNT(*) FROM checkins WHERE deleted_at IS NULL AND created_at >= ?",
                (year_start,),
            ).fetchone()[0]
            distinct_persons = self._conn.execute(
                "SELECT COUNT(DISTINCT person_id) FROM checkins "
                "WHERE deleted_at IS NULL AND person_id IS NOT NULL"
            ).fetchone()[0]
            guest_count = self._conn.execute(
                "SELECT COUNT(*) FROM checkins WHERE deleted_at IS NULL AND person_id IS NULL"
            ).fetchone()[0]
            bounds = self._conn.execute(
                "SELECT MIN(created_at), MAX(created_at) FROM checkins WHERE deleted_at IS NULL"
            ).fetchone()
        return CheckinStats(
            total_checkins=total,
            checkins_this_year=this_year,
            distinct_persons=distinct_persons,
            guest_count=guest_count,
            first_checkin_at=bounds[0],
            most_recent_checkin_at=bounds[1],
        )

    def list_checkin_photos(self, *, limit: int) -> list[Checkin]:
        """Non-deleted check-ins that reference a photo, newest first.

        Only the photo *reference* is returned; whether that photo may be shown
        publicly is decided by the caller by intersecting with owner-approved
        gallery photos. Indexed by ``idx_checkins_photo``.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, person_id, label, photo_recording_id, created_at, deleted_at "
                "FROM checkins WHERE deleted_at IS NULL AND photo_recording_id IS NOT NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_checkin(row) for row in rows]

    @staticmethod
    def _row_to_checkin(row: tuple) -> Checkin:
        return Checkin(
            id=row[0],
            person_id=row[1],
            label=row[2],
            photo_recording_id=row[3],
            created_at=row[4],
            deleted_at=row[5],
        )

    # ------------------------------------------------------------------
    # Moderation log (audit trail — append-only)
    # ------------------------------------------------------------------

    def append_moderation_log(
        self,
        *,
        log_id: str,
        target_kind: str,
        target_id: str,
        action: str,
        actor: str,
        created_at: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO moderation_log "
                "(id, target_kind, target_id, action, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (log_id, target_kind, target_id, action, actor, created_at),
            )
            self._conn.commit()

    def list_moderation_log(self, *, limit: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, target_kind, target_id, action, actor, created_at FROM moderation_log "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "target_kind": r[1],
                "target_id": r[2],
                "action": r[3],
                "actor": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
