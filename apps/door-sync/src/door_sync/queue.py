"""Durable upload queue — SQLite (WAL) on the SSD.

This is the crash-consistency core of door-sync (T-502 / ADR-0007). Every state
transition is a single committed SQLite transaction, and the *engine* performs
each external side effect (upload, checksum verify, license-deletion callback)
**before** committing the state that reflects it — never after — so a ``kill -9``
at any instant leaves a state the engine can safely resume from:

  - crash before an upload's outcome is recorded → item stays ``pending`` and is
    retried; the far side is overwritten (deterministic ``dest_key``) so retry
    never duplicates.
  - crash after a clip upload is marked ``completed`` but before door-media is
    told it may delete (``licensed=0``) → recovery re-notifies (idempotent).
    door-media never deleted anything because it was never told to.

Durability: ``journal_mode=WAL`` + ``synchronous=FULL``. FULL (not NORMAL) is
deliberate — the door appliance can lose power at any moment, and the whole
point of this queue is "never lose a clip"; the write volume (a few items a
minute) makes the extra fsync cost irrelevant.

The queue stores only opaque references to media (SSD-relative paths that passed
the biometric fence) and already-serialized contract events; it holds no
identity/biometric data itself.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

ItemKind = Literal["clip", "thumbnail", "event", "purge"]
ItemStatus = Literal["pending", "completed", "dead_letter"]
Target = Literal["nas", "nuc"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_item (
    item_id            TEXT PRIMARY KEY,
    kind               TEXT NOT NULL,
    target             TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending',
    dest_key           TEXT NOT NULL,
    recording_id       TEXT,
    local_path         TEXT,
    expected_sha256    TEXT,
    payload            TEXT,
    attempts           INTEGER NOT NULL DEFAULT 0,
    permanent_failures INTEGER NOT NULL DEFAULT 0,
    next_attempt_at    REAL NOT NULL DEFAULT 0,
    last_error         TEXT,
    error_class        TEXT,
    verified_sha256    TEXT,
    licensed           INTEGER NOT NULL DEFAULT 0,
    trace_id           TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    completed_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_queue_status_due
    ON queue_item(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_queue_status_licensed
    ON queue_item(status, kind, licensed);
CREATE INDEX IF NOT EXISTS idx_queue_target_status
    ON queue_item(target, status);
"""

_COLUMNS = (
    "item_id",
    "kind",
    "target",
    "status",
    "dest_key",
    "recording_id",
    "local_path",
    "expected_sha256",
    "payload",
    "attempts",
    "permanent_failures",
    "next_attempt_at",
    "last_error",
    "error_class",
    "verified_sha256",
    "licensed",
    "trace_id",
    "created_at",
    "updated_at",
    "completed_at",
)


@dataclass(frozen=True, slots=True)
class QueueItem:
    item_id: str
    kind: str
    target: str
    status: str
    dest_key: str
    recording_id: str | None
    local_path: str | None
    expected_sha256: str | None
    payload: str | None
    attempts: int
    permanent_failures: int
    next_attempt_at: float
    last_error: str | None
    error_class: str | None
    verified_sha256: str | None
    licensed: int
    trace_id: str
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class NewItem:
    """A queue item to enqueue. ``item_id`` is deterministic per logical item."""

    item_id: str
    kind: ItemKind
    target: Target
    dest_key: str
    trace_id: str
    recording_id: str | None = None
    local_path: str | None = None
    expected_sha256: str | None = None
    payload: str | None = None


@dataclass(frozen=True, slots=True)
class QueueStats:
    pending: int
    completed: int
    dead_letter: int
    oldest_pending_age_s: int
    per_target: dict[str, dict[str, int]]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class UploadQueue:
    """Thread-safe durable queue. One connection, one lock (writes are serial)."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    def enqueue(self, item: NewItem) -> bool:
        """Insert an item. Idempotent by ``item_id``: a re-enqueue of an item
        that already exists (in any state) is a no-op and returns ``False``.
        """
        now = _now_iso()
        with self._lock:
            cur = self._conn.execute(
                """INSERT OR IGNORE INTO queue_item
                   (item_id, kind, target, status, dest_key, recording_id,
                    local_path, expected_sha256, payload, next_attempt_at,
                    trace_id, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (
                    item.item_id,
                    item.kind,
                    item.target,
                    item.dest_key,
                    item.recording_id,
                    item.local_path,
                    item.expected_sha256,
                    item.payload,
                    item.trace_id,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Claim / read
    # ------------------------------------------------------------------

    def due_items(self, *, now_epoch: float, target: str | None = None) -> list[QueueItem]:
        """Pending items whose backoff has elapsed, oldest first."""
        query = "SELECT * FROM queue_item WHERE status='pending' AND next_attempt_at <= ?"
        params: list[object] = [now_epoch]
        if target is not None:
            query += " AND target = ?"
            params.append(target)
        query += " ORDER BY created_at ASC, item_id ASC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row(r) for r in rows]

    def items_awaiting_license(self) -> list[QueueItem]:
        """Clips uploaded+verified but not yet confirmed to door-media.

        These are the crash-window items recovery must re-notify (idempotently).
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM queue_item
                   WHERE status='completed' AND kind='clip' AND licensed=0
                   ORDER BY completed_at ASC""",
            ).fetchall()
        return [self._row(r) for r in rows]

    def get(self, item_id: str) -> QueueItem | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM queue_item WHERE item_id=?", (item_id,)
            ).fetchone()
        return self._row(row) if row is not None else None

    def list_items(self, *, limit: int = 200) -> list[QueueItem]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM queue_item ORDER BY created_at ASC, item_id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row(r) for r in rows]

    # ------------------------------------------------------------------
    # State transitions (each a single committed transaction)
    # ------------------------------------------------------------------

    def mark_completed(self, item_id: str, *, verified_sha256: str | None, licensed: bool) -> None:
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                """UPDATE queue_item
                   SET status='completed', verified_sha256=?, licensed=?,
                       completed_at=?, updated_at=?, last_error=NULL, error_class=NULL,
                       attempts=attempts+1
                   WHERE item_id=? AND status='pending'""",
                (verified_sha256, 1 if licensed else 0, now, now, item_id),
            )
            self._conn.commit()

    def mark_licensed(self, item_id: str) -> None:
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                "UPDATE queue_item SET licensed=1, updated_at=? WHERE item_id=?",
                (now, item_id),
            )
            self._conn.commit()

    def record_failure(
        self,
        item_id: str,
        *,
        permanent: bool,
        next_attempt_at: float,
        error_class: str,
        message: str,
        max_permanent_attempts: int,
    ) -> ItemStatus:
        """Record a failed attempt. Returns the item's status afterwards.

        Transient failures (target down) retry forever within backoff bounds and
        never dead-letter — a multi-day outage must drain on recovery. Only
        *permanent* failures accrue toward ``max_permanent_attempts``; hitting
        that cap moves the item to ``dead_letter`` (surfaced, never dropped).
        """
        now = _now_iso()
        with self._lock:
            row = self._conn.execute(
                "SELECT permanent_failures FROM queue_item WHERE item_id=?",
                (item_id,),
            ).fetchone()
            if row is None:
                return "dead_letter"
            perm = row["permanent_failures"] + (1 if permanent else 0)
            dead = permanent and perm >= max_permanent_attempts
            status: ItemStatus = "dead_letter" if dead else "pending"
            self._conn.execute(
                """UPDATE queue_item
                   SET status=?, attempts=attempts+1, permanent_failures=?,
                       next_attempt_at=?, last_error=?, error_class=?, updated_at=?
                   WHERE item_id=?""",
                (status, perm, next_attempt_at, message[:500], error_class, now, item_id),
            )
            self._conn.commit()
        return status

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def prune_completed(self, *, older_than_iso: str) -> int:
        """Delete finalized-completed items older than a cutoff to bound growth.

        Clips are only pruned once ``licensed=1`` (door-media confirmed) so a
        clip whose license callback is still outstanding is never forgotten.
        Dead-letters are never pruned here — they require attention.
        """
        with self._lock:
            cur = self._conn.execute(
                """DELETE FROM queue_item
                   WHERE status='completed'
                     AND licensed=1
                     AND completed_at IS NOT NULL
                     AND completed_at < ?""",
                (older_than_iso,),
            )
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self, *, now_epoch: float) -> QueueStats:
        with self._lock:
            status_rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM queue_item GROUP BY status"
            ).fetchall()
            target_rows = self._conn.execute(
                "SELECT target, status, COUNT(*) AS n FROM queue_item GROUP BY target, status"
            ).fetchall()
            oldest = self._conn.execute(
                "SELECT MIN(created_at) AS c FROM queue_item WHERE status='pending'"
            ).fetchone()
        counts = {r["status"]: r["n"] for r in status_rows}
        per_target: dict[str, dict[str, int]] = {}
        for r in target_rows:
            per_target.setdefault(r["target"], {})[r["status"]] = r["n"]
        oldest_age = 0
        if oldest is not None and oldest["c"] is not None:
            try:
                created = datetime.fromisoformat(oldest["c"])
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                oldest_age = max(0, int(datetime.now(UTC).timestamp() - created.timestamp()))
            except (ValueError, OverflowError):
                oldest_age = 0
        return QueueStats(
            pending=counts.get("pending", 0),
            completed=counts.get("completed", 0),
            dead_letter=counts.get("dead_letter", 0),
            oldest_pending_age_s=oldest_age,
            per_target=per_target,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row(row: sqlite3.Row) -> QueueItem:
        return QueueItem(**{col: row[col] for col in _COLUMNS})


def iter_dead_letters(items: Iterable[QueueItem]) -> list[QueueItem]:
    return [i for i in items if i.status == "dead_letter"]
