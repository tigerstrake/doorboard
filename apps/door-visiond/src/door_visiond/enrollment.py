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

import base64
import hashlib
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

-- Remote-enrollment invites (ADR-0016 §4). This table lives here, rather than in
-- its own database, so that consuming an invite and inserting the person it
-- authorized are one transaction (E-11) — an invite cannot be spent twice by
-- concurrent pickups. It also inherits this file's secure_delete and its place
-- on the encrypted volume.
CREATE TABLE IF NOT EXISTS relay_invite (
    invite_id        TEXT PRIMARY KEY,   -- 'inv_' + base62
    secret_sha256    TEXT NOT NULL,      -- base64url sha256; the secret is never stored
    label            TEXT,               -- admin's own note; never sent to the relay
    max_images       INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    consumed_at      TEXT,
    revoked_at       TEXT,
    person_id        TEXT                -- set on consumption: the enrollment it produced
);

CREATE INDEX IF NOT EXISTS idx_embedding_person ON embedding(person_id);
"""


def _base62(n: int = 22) -> str:
    return "".join(secrets.choice(_BASE62) for _ in range(n))


def new_person_id() -> str:
    return "prs_" + _base62()


def new_embedding_id() -> str:
    return "emb_" + _base62()


def new_invite_id() -> str:
    return "inv_" + _base62()


def new_invite_secret() -> str:
    """32 CSPRNG bytes, base64url unpadded. Returned once, never stored (ADR-0016 §4)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def hash_invite_secret(secret: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()).decode().rstrip("=")


class InviteUnusableError(Exception):
    """An invite cannot authorize an enrollment. ``reason`` is machine-readable only."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class InviteConsumption:
    """Proof that a remote enrollee held the invite secret (ADR-0016 §4 step 2)."""

    invite_id: str
    secret_sha256: str


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

    def __init__(self, db_path: Path | str) -> None:
        if isinstance(db_path, Path):
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
        invite: InviteConsumption | None = None,
    ) -> str:
        """Insert one person + their embeddings + profile atomically.

        ``embeddings`` is a list of (embedding, model_id, quality).  Returns the
        generated opaque ``person_id``.

        When ``invite`` is given (the remote path), the invite is validated and
        marked consumed **inside the same transaction** as the insert, so two
        concurrent pickups of one invite enroll exactly once (ADR-0016 E-11).
        Raises ``InviteUnusableError`` without inserting anything if the invite is
        unknown, expired, revoked, already consumed, or the secret hash mismatches.
        """
        if not embeddings:
            msg = "at least one embedding is required to enroll"
            raise ValueError(msg)

        person_id = new_person_id()
        now = datetime.now(UTC).isoformat()
        consent_iso = consent_at.astimezone(UTC).isoformat()

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE" if invite is not None else "BEGIN")
                if invite is not None:
                    self._claim_invite_locked(invite, person_id=person_id, now=now)
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
    # Remote-enrollment invites (ADR-0016 §4)
    # ------------------------------------------------------------------

    def create_invite(
        self,
        *,
        expires_at: datetime,
        label: str | None = None,
        max_images: int = 5,
    ) -> tuple[str, str]:
        """Mint a single-use invite. Returns ``(invite_id, secret)``.

        The secret is returned exactly once — only its hash is persisted, so a
        stolen enrollment DB does not yield working invite URLs.
        """
        invite_id = new_invite_id()
        secret = new_invite_secret()
        with self._lock:
            self._conn.execute(
                "INSERT INTO relay_invite "
                "(invite_id, secret_sha256, label, max_images, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    invite_id,
                    hash_invite_secret(secret),
                    label,
                    max_images,
                    datetime.now(UTC).isoformat(),
                    expires_at.astimezone(UTC).isoformat(),
                ),
            )
            self._conn.commit()
        logger.info("relay_invite_created", extra={"invite_id": invite_id})
        return invite_id, secret

    def _claim_invite_locked(self, invite: InviteConsumption, *, person_id: str, now: str) -> None:
        """Validate and consume an invite. Caller holds the lock and an open transaction."""
        row = self._conn.execute(
            "SELECT secret_sha256, expires_at, consumed_at, revoked_at, max_images "
            "FROM relay_invite WHERE invite_id=?",
            (invite.invite_id,),
        ).fetchone()
        if row is None:
            raise InviteUnusableError("unknown_invite")
        secret_sha256, expires_at, consumed_at, revoked_at, _max_images = row
        if consumed_at is not None:
            raise InviteUnusableError("invite_already_consumed")
        if revoked_at is not None:
            raise InviteUnusableError("invite_revoked")
        if datetime.fromisoformat(expires_at) <= datetime.now(UTC):
            raise InviteUnusableError("invite_expired")
        if not secrets.compare_digest(secret_sha256, invite.secret_sha256):
            raise InviteUnusableError("invite_secret_mismatch")
        self._conn.execute(
            "UPDATE relay_invite SET consumed_at=?, person_id=? WHERE invite_id=?",
            (now, person_id, invite.invite_id),
        )

    def invite_max_images(self, invite_id: str) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT max_images FROM relay_invite WHERE invite_id=?", (invite_id,)
            ).fetchone()
        return int(row[0]) if row else None

    def revoke_invite(self, invite_id: str) -> bool:
        """Revoke an unconsumed invite. Consumed invites are history and stay as they are."""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE relay_invite SET revoked_at=? "
                "WHERE invite_id=? AND consumed_at IS NULL AND revoked_at IS NULL",
                (datetime.now(UTC).isoformat(), invite_id),
            )
            self._conn.commit()
        logger.info(
            "relay_invite_revoked",
            extra={"invite_id": invite_id, "changed": cursor.rowcount},
        )
        return cursor.rowcount > 0

    def list_invites(self, *, include_closed: bool = False) -> list[dict[str, object]]:
        query = (
            "SELECT invite_id, label, max_images, created_at, expires_at, "
            "       consumed_at, revoked_at, person_id FROM relay_invite"
        )
        if not include_closed:
            query += " WHERE consumed_at IS NULL AND revoked_at IS NULL"
        query += " ORDER BY created_at DESC"
        now = datetime.now(UTC)
        with self._lock:
            rows = self._conn.execute(query).fetchall()
        invites: list[dict[str, object]] = []
        for r in rows:
            expires_at = r[4]
            if r[5] is not None:
                status = "consumed"
            elif r[6] is not None:
                status = "revoked"
            elif datetime.fromisoformat(expires_at) <= now:
                status = "expired"
            else:
                status = "open"
            invites.append(
                {
                    "invite_id": r[0],
                    "label": r[1],
                    "max_images": r[2],
                    "created_at": r[3],
                    "expires_at": expires_at,
                    "consumed_at": r[5],
                    "revoked_at": r[6],
                    "person_id": r[7],
                    "status": status,
                }
            )
        return invites

    def open_invite_registrations(self) -> list[tuple[str, str, str, int]]:
        """``(invite_id, secret_sha256, expires_at, max_images)`` for still-usable invites.

        Used to re-register invites with the relay after a relay-side outage or
        KV expiry. Only the hash travels, never the secret.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT invite_id, secret_sha256, expires_at, max_images FROM relay_invite "
                "WHERE consumed_at IS NULL AND revoked_at IS NULL AND expires_at > ?",
                (datetime.now(UTC).isoformat(),),
            ).fetchall()
        return [(r[0], r[1], r[2], int(r[3])) for r in rows]

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

    def list_people(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.person_id, p.display_name, p.consent_version, p.consent_at, "
                "       p.created_at, pr.profile_id, pr.color, pr.sound "
                "FROM person p LEFT JOIN profile pr ON pr.person_id = p.person_id"
            ).fetchall()
            return [
                {
                    "person_id": r[0],
                    "display_name": r[1],
                    "consent_version": r[2],
                    "consent_at": r[3],
                    "created_at": r[4],
                    "profile_id": r[5],
                    "color": r[6],
                    "sound": r[7],
                }
                for r in rows
            ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
