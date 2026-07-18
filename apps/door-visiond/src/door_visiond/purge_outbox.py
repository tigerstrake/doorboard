from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PendingPurge:
    person_id: str
    attempts: int


class PurgeOutbox:
    """Crash-safe queue for eventually consistent NUC person-data purges."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS purge_outbox ("
                "person_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0, "
                "next_attempt_epoch REAL NOT NULL, last_error TEXT, created_epoch REAL NOT NULL)"
            )
            self._conn.commit()

    def enqueue(self, person_id: str) -> bool:
        now = time.time()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO purge_outbox "
                "(person_id, attempts, next_attempt_epoch, created_epoch) VALUES (?, 0, ?, ?)",
                (person_id, now, now),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def pending(self, *, limit: int = 16, now_epoch: float | None = None) -> list[PendingPurge]:
        now = time.time() if now_epoch is None else now_epoch
        with self._lock:
            rows = self._conn.execute(
                "SELECT person_id, attempts FROM purge_outbox "
                "WHERE next_attempt_epoch <= ? ORDER BY created_epoch LIMIT ?",
                (now, limit),
            ).fetchall()
        return [PendingPurge(person_id=row[0], attempts=row[1]) for row in rows]

    def mark_delivered(self, person_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM purge_outbox WHERE person_id = ?", (person_id,))
            self._conn.commit()

    def mark_failed(self, person_id: str, *, attempts: int, delay_s: float, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE purge_outbox SET attempts = ?, next_attempt_epoch = ?, last_error = ? "
                "WHERE person_id = ?",
                (attempts, time.time() + delay_s, error[:500], person_id),
            )
            self._conn.commit()

    def depth(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM purge_outbox").fetchone()
        return int(row[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
