"""Enrollment store — the enrolled, consented face set (ADR-0009 §1, §3).

One SQLite database, WAL mode, on the SSD (never microSD, never NAS).  Opened
with ``secure_delete=ON`` so freed pages are zeroed — load-bearing for the
deletion semantics in §3.  Unknown embeddings never come near this file; only
manually enrolled, consenting people are stored here.

Deletion (E-5): unenroll deletes the person row (cascading to embeddings +
profile), writes an opaque tombstone, commits, then truncates the WAL with a
checkpoint so no stale biometric bytes survive in the -wal/-shm sidecars.
"""

from __future__ import annotations

import secrets
import sqlite3
import string
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from door_visiond.embedding import Embedding
from door_visiond.logging_setup import get_logger

logger = get_logger("door_visiond.enrollment")

_BASE62 = string.digits + string.ascii_lowercase + string.ascii_uppercase

_SCHEMA = """
CREATE TABLE IF NOT EXISTS person (
    person_id        TEXT PRIMARY KEY,
    display_name     TEXT NOT NULL,
    consent_version  TEXT NOT NULL,
    consent_at       TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embedding (
    embedding_id     TEXT PRIMARY KEY,
    person_id        TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    vector           BLOB NOT NULL,
    model_id         TEXT NOT NULL,
    quality          REAL NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile (
    person_id        TEXT PRIMARY KEY REFERENCES person(person_id) ON DELETE CASCADE,
    profile_id       TEXT NOT NULL UNIQUE,
    color            TEXT NOT NULL,
    sound            TEXT
);

CREATE TABLE IF NOT EXISTS revocation_tombstone (
    person_id        TEXT PRIMARY KEY,
    consent_at       TEXT NOT NULL,
    revoked_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_embedding_person ON embedding(person_id);
"""


def _base62(n: int = 22) -> str:
    return "".join(secrets.choice(_BASE62) for _ in range(n))


def new_person_id() -> str:
    return "prs_" + _base62()


def new_embedding_id() -> str:
    return "emb_" + _base62()


@dataclass(frozen=True)
class ProfileSpec:
    profile_id: str
    color: str
    sound: str | None = None


@dataclass(frozen=True)
class EnrolledPerson:
    person_id: str
    display_name: str
    profile_id: str
    color: str
    sound: str | None
    vectors: tuple[Embedding, ...]


@dataclass(frozen=True)
class Tombstone:
    person_id: str
    consent_at: str
    revoked_at: str


class EnrollmentStore:
    """Thread-safe SQLite enrollment registry with secure-delete semantics."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._configure(self._conn)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("enrollment_db_opened", extra={"path": str(db_path)})

    @staticmethod
    def _configure(conn: sqlite3.Connection) -> None:
        # secure_delete is load-bearing for §3 (freed pages are zeroed).
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")

    # ------------------------------------------------------------------
    # Enrollment
    # ------------------------------------------------------------------

    def enroll(
        self,
        *,
        display_name: str,
        consent_version: str,
        consent_at: datetime,
        embeddings: list[tuple[Embedding, str, float]],
        profile: ProfileSpec,
    ) -> str:
        """Insert one person + their embeddings + profile atomically.

        ``embeddings`` is a list of (embedding, model_id, quality).  Returns the
        generated opaque ``person_id``.
        """
        if not embeddings:
            msg = "at least one embedding is required to enroll"
            raise ValueError(msg)

        person_id = new_person_id()
        now = datetime.now(UTC).isoformat()
        consent_iso = consent_at.astimezone(UTC).isoformat()

        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute(
                    "INSERT INTO person "
                    "(person_id, display_name, consent_version, consent_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (person_id, display_name, consent_version, consent_iso, now),
                )
                for emb, model_id, quality in embeddings:
                    self._conn.execute(
                        "INSERT INTO embedding "
                        "(embedding_id, person_id, vector, model_id, quality, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            new_embedding_id(),
                            person_id,
                            emb.to_le_float32_bytes(),
                            model_id,
                            quality,
                            now,
                        ),
                    )
                self._conn.execute(
                    "INSERT INTO profile (person_id, profile_id, color, sound) VALUES (?, ?, ?, ?)",
                    (person_id, profile.profile_id, profile.color, profile.sound),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        logger.info(
            "person_enrolled",
            extra={"person_id": person_id, "embeddings": len(embeddings)},
        )
        return person_id

    # ------------------------------------------------------------------
    # Deletion (E-5: purge + tombstone + checkpoint-truncate)
    # ------------------------------------------------------------------

    def unenroll(self, person_id: str, *, consent_at: str | None = None) -> bool:
        """Delete a person and all biometric rows; leave only an opaque tombstone.

        Returns True if a person row existed.  After the delete+commit the WAL is
        truncated via checkpoint so no biometric bytes remain in the sidecars.
        """
        revoked_at = datetime.now(UTC).isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT consent_at FROM person WHERE person_id=?", (person_id,)
            ).fetchone()
            existed = row is not None
            resolved_consent_at = consent_at or (row[0] if row else revoked_at)

            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM person WHERE person_id=?", (person_id,))
                self._conn.execute(
                    "INSERT OR REPLACE INTO revocation_tombstone "
                    "(person_id, consent_at, revoked_at) VALUES (?, ?, ?)",
                    (person_id, resolved_consent_at, revoked_at),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

            # Zero out freed pages left in the write-ahead log.
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.commit()

        logger.info("person_unenrolled", extra={"person_id": person_id, "existed": existed})
        return existed

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def load_enrolled(self) -> list[EnrolledPerson]:
        """Load every enrolled person with their vectors (for the in-memory matcher)."""
        with self._lock:
            people = self._conn.execute(
                "SELECT p.person_id, p.display_name, pr.profile_id, pr.color, pr.sound "
                "FROM person p JOIN profile pr ON pr.person_id = p.person_id"
            ).fetchall()
            result: list[EnrolledPerson] = []
            for person_id, display_name, profile_id, color, sound in people:
                vectors = [
                    Embedding.from_le_float32_bytes(blob)
                    for (blob,) in self._conn.execute(
                        "SELECT vector FROM embedding WHERE person_id=?", (person_id,)
                    ).fetchall()
                ]
                result.append(
                    EnrolledPerson(
                        person_id=person_id,
                        display_name=display_name,
                        profile_id=profile_id,
                        color=color,
                        sound=sound,
                        vectors=tuple(vectors),
                    )
                )
        return result

    def person_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM person").fetchone()[0])

    def embedding_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM embedding").fetchone()[0])

    def get_tombstone(self, person_id: str) -> Tombstone | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT person_id, consent_at, revoked_at FROM revocation_tombstone "
                "WHERE person_id=?",
                (person_id,),
            ).fetchone()
        return Tombstone(*row) if row else None

    def profile_exists(self, profile_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM profile WHERE profile_id=?", (profile_id,)
            ).fetchone()
        return row is not None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
