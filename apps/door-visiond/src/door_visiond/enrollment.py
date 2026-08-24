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
import re
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

# The effects catalogue (T-103), mirroring the choices the enrollment surfaces
# offer. `profile.profile_id` is UNIQUE (ADR-0009 §1) so each person gets a
# distinguishable light — but two people picking the same colour must not be an
# error, so a taken choice is reassigned to the next free entry rather than
# rejected.
#
# EVERY id here MUST be an effect the firmware actually plays (its
# `door_effect_from_name` in firmware/esp32-door-controller/.../door_effects.c) — an
# unknown id resolves to DOOR_EFFECT_NONE and the light silently falls back to
# blue_wave. Four ids used to be fiction (warm_amber/violet_dusk/coral_glow/cool_white),
# so two-thirds of enrollees got blue whatever they picked; the catalogue is now the
# firmware's six personalisation effects. `tests/firmware`/door-visiond assert this
# against the firmware source so it cannot drift again. The colour is the screen accent
# (ADR-0021), paired with the light that suits it.
PROFILE_CATALOG: tuple[tuple[str, str], ...] = (
    ("sunrise", "#ffb300"),
    ("blue_wave", "#3a86ff"),
    ("green_pulse", "#3ddc84"),
    ("rainbow", "#9b5de5"),
    ("mint_pulse", "#2ec4b6"),
    ("sparkle", "#e8eef5"),
)


class NoProfileAvailableError(Exception):
    """Every entry in the effects catalogue is already assigned."""


class InvalidAccentColorError(Exception):
    """The requested accent colour is not a hex literal (ADR-0021)."""


_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_accent_color(value: str | None) -> str | None:
    """Validate an enrollee-chosen colour, or raise.

    Colour is now attacker-chosen input that ends up in a CSS custom property, so it is
    constrained to a strict hex literal here at the boundary rather than trusted and
    escaped later. "The enrollee picks their colour" and "the enrollee picks arbitrary
    CSS" are one missing check apart (ADR-0021 §Consequences).

    Returns lower-case ``#rrggbb``/``#rgb``, or None for "no preference".
    """
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if not _HEX_COLOR.match(candidate):
        raise InvalidAccentColorError(f"not a hex colour: {candidate!r}")
    return candidate.lower()


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
    sound            TEXT,
    -- The colour the enrollee actually chose (ADR-0021). Deliberately NOT unique and
    -- never reassigned: `profile_id` names an ESP32 LED effect and must stay unique, but
    -- two people are welcome to the same colour on screen. `color` above remains the
    -- catalogue colour of the assigned effect and is the fallback when this is NULL.
    accent_color     TEXT
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

-- Arrival log (ADR-0018 §1). This lives HERE, in the enrollment database, rather
-- than beside check-ins in door-api's social DB, and that placement is
-- load-bearing rather than convenient:
--
--   * ON DELETE CASCADE means unenrolling destroys a person's arrival history in
--     the same transaction as their embeddings (E-21). Retention is unbounded by
--     owner decision, so a copy in the social DB would outlive revoked consent
--     forever, silently — the failure mode ADR-0005 §6 exists to prevent.
--   * This file is on the LUKS volume (ADR-0009 §6), which is where unbounded
--     presence history belongs given the stolen-Pi threat model.
--   * secure_delete=ON applies, so purged visit rows are zeroed like vectors.
--
-- One row per *visit*, not per sighting: sightings within the merge window extend
-- last_seen_at on the open visit (E-22).
CREATE TABLE IF NOT EXISTS visit (
    visit_id         TEXT PRIMARY KEY,   -- 'vst_' + base62
    person_id        TEXT NOT NULL REFERENCES person(person_id) ON DELETE CASCADE,
    arrived_at       TEXT NOT NULL,      -- UTC ISO-8601
    last_seen_at     TEXT NOT NULL       -- extended while they remain visible
);

CREATE INDEX IF NOT EXISTS idx_embedding_person ON embedding(person_id);
CREATE INDEX IF NOT EXISTS idx_visit_person_seen ON visit(person_id, last_seen_at DESC);
"""


def _base62(n: int = 22) -> str:
    return "".join(secrets.choice(_BASE62) for _ in range(n))


def new_person_id() -> str:
    return "prs_" + _base62()


def new_embedding_id() -> str:
    return "emb_" + _base62()


def new_invite_id() -> str:
    return "inv_" + _base62()


def new_visit_id() -> str:
    return "vst_" + _base62()


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


class DisplayNameTakenError(Exception):
    """Someone enrolled already answers to this name (ADR-0019 §2).

    Raised inside the enrolling transaction, so it is a uniqueness violation rather
    than a pre-flight opinion: two enrollments of one name cannot interleave past it.
    """

    def __init__(self, display_name: str) -> None:
        self.display_name = display_name
        super().__init__("that name is already enrolled at this door")


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
    # What the enrollee picked, when they picked one (ADR-0021). None means "use the
    # catalogue colour of whichever effect gets assigned", which is the old behaviour.
    accent_color: str | None = None


@dataclass(frozen=True)
class EnrolledPerson:
    person_id: str
    display_name: str
    profile_id: str
    color: str
    sound: str | None
    vectors: tuple[Embedding, ...]
    # Which statement they enrolled under. Carried all the way to the identity
    # event so door-api can gate attribution on it (ADR-0018).
    consent_version: str = ""
    # The chosen colour (ADR-0021), or None for a row enrolled before it existed whose
    # colour was only ever the catalogue's. Consumers fall back to `color`.
    accent_color: str | None = None


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
        self._migrate(self._conn)
        self._conn.commit()
        # Set by enroll() so callers can tell the enrollee their colour changed.
        self.last_assigned_profile: str = ""
        self.last_profile_was_reassigned: bool = False
        logger.info("enrollment_db_opened", extra={"path": str(db_path)})

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Bring an existing database up to the current schema.

        ``CREATE TABLE IF NOT EXISTS`` silently does nothing to a table that already
        exists, so a new column needs an explicit ``ALTER``. Guarded by the current
        column list rather than a version counter: the schema has never carried one, and
        inventing one now would need a migration of its own to backfill.

        The only migration so far (ADR-0021) adds ``accent_color`` and backfills it from
        the catalogue colour already stored on the row — so the day it runs, every
        enrolled person's screens look exactly as they did before.
        """
        columns = {row[1] for row in conn.execute("PRAGMA table_info(profile)").fetchall()}
        if "accent_color" not in columns:
            conn.execute("ALTER TABLE profile ADD COLUMN accent_color TEXT")
            conn.execute("UPDATE profile SET accent_color = color WHERE accent_color IS NULL")
            logger.info("enrollment_db_migrated", extra={"added": "profile.accent_color"})

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
                # Uniqueness, so it belongs in the transaction that inserts: checking
                # outside it lets two enrollments of one name interleave and both win.
                # Ordered AFTER the invite claim on purpose -- a replayed bundle must
                # be told its invite is spent, not that the name is taken, which would
                # tell whoever holds a dead invite who lives here (ADR-0019 §2).
                if self._display_name_taken_locked(display_name):
                    raise DisplayNameTakenError(display_name)
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
                assigned = self._allocate_profile_locked(profile)
                self._conn.execute(
                    "INSERT INTO profile "
                    "(person_id, profile_id, color, sound, accent_color) VALUES (?, ?, ?, ?, ?)",
                    (
                        person_id,
                        assigned.profile_id,
                        assigned.color,
                        assigned.sound,
                        assigned.accent_color or assigned.color,
                    ),
                )
                reassigned = assigned.profile_id != profile.profile_id
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

        logger.info(
            "person_enrolled",
            extra={
                "person_id": person_id,
                "embeddings": len(embeddings),
                "profile_id": assigned.profile_id,
                "profile_reassigned": reassigned,
            },
        )
        self.last_assigned_profile = assigned.profile_id
        self.last_profile_was_reassigned = reassigned
        return person_id

    def _allocate_profile_locked(self, preferred: ProfileSpec) -> ProfileSpec:
        """Resolve a free profile id. Caller holds the lock and an open transaction.

        Two people wanting the same colour is ordinary, not exceptional: the second
        one gets the next free entry instead of an IntegrityError. Resolved inside
        the caller's transaction so a concurrent enrollment cannot claim the same id
        between the check and the insert.
        """
        taken = {row[0] for row in self._conn.execute("SELECT profile_id FROM profile").fetchall()}
        if preferred.profile_id not in taken:
            return preferred
        for profile_id, color in PROFILE_CATALOG:
            if profile_id not in taken:
                # Reassigning the LED effect must NOT move the enrollee's colour with it
                # (ADR-0021). Previously it did, so asking for amber second got you
                # violet on every screen without being told.
                return ProfileSpec(
                    profile_id=profile_id,
                    color=color,
                    sound=preferred.sound,
                    accent_color=preferred.accent_color or preferred.color,
                )
        raise NoProfileAvailableError(f"all {len(PROFILE_CATALOG)} catalogue profiles are assigned")

    # ------------------------------------------------------------------
    # Arrival log (ADR-0018 §1)
    # ------------------------------------------------------------------

    def record_sighting(
        self,
        person_id: str,
        *,
        now: datetime,
        merge_window_s: float,
    ) -> tuple[str, bool]:
        """Log that a person is at the door. Returns ``(visit_id, opened_new)``.

        Sightings inside ``merge_window_s`` of this person's most recent one extend
        that visit rather than starting another (E-22) — someone lingering in the
        hallway is one visit, not one per recognition cycle.

        Silently does nothing if the person is not enrolled (the FK would reject
        it anyway); a visit for a non-existent person is a bug elsewhere, and
        raising here would be on the recognition path.
        """
        now_iso = now.astimezone(UTC).isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT visit_id, last_seen_at FROM visit WHERE person_id=? "
                "ORDER BY last_seen_at DESC LIMIT 1",
                (person_id,),
            ).fetchone()

            if row is not None:
                visit_id, last_seen_at = row
                gap_s = (now - datetime.fromisoformat(last_seen_at)).total_seconds()
                if 0 <= gap_s <= merge_window_s:
                    self._conn.execute(
                        "UPDATE visit SET last_seen_at=? WHERE visit_id=?",
                        (now_iso, visit_id),
                    )
                    self._conn.commit()
                    return str(visit_id), False

            if (
                self._conn.execute(
                    "SELECT 1 FROM person WHERE person_id=?", (person_id,)
                ).fetchone()
                is None
            ):
                return "", False

            visit_id = new_visit_id()
            self._conn.execute(
                "INSERT INTO visit (visit_id, person_id, arrived_at, last_seen_at) "
                "VALUES (?, ?, ?, ?)",
                (visit_id, person_id, now_iso, now_iso),
            )
            self._conn.commit()
        logger.info("visit_opened", extra={"person_id": person_id, "visit_id": visit_id})
        return visit_id, True

    def list_visits(
        self, *, limit: int = 200, person_id: str | None = None
    ) -> list[dict[str, object]]:
        """Most recent visits first, joined to the display name for admin views."""
        query = (
            "SELECT v.visit_id, v.person_id, p.display_name, v.arrived_at, "
            "       v.last_seen_at "
            "FROM visit v JOIN person p ON p.person_id = v.person_id"
        )
        params: list[object] = []
        if person_id is not None:
            query += " WHERE v.person_id=?"
            params.append(person_id)
        query += " ORDER BY v.last_seen_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "visit_id": r[0],
                "person_id": r[1],
                "display_name": r[2],
                "arrived_at": r[3],
                "last_seen_at": r[4],
            }
            for r in rows
        ]

    def visit_counts(self) -> list[dict[str, object]]:
        """Per-person visit totals. Admin-only — never a public payload (E-24)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT v.person_id, p.display_name, COUNT(*), MAX(v.last_seen_at) "
                "FROM visit v JOIN person p ON p.person_id = v.person_id "
                "GROUP BY v.person_id ORDER BY COUNT(*) DESC"
            ).fetchall()
        return [
            {"person_id": r[0], "display_name": r[1], "visits": int(r[2]), "last_seen_at": r[3]}
            for r in rows
        ]

    def purge_visits(self, *, person_id: str | None = None) -> int:
        """Delete arrival history, for one person or all of it.

        Unenroll already cascades; this is the separate 'forget where I've been but
        keep recognising me' control the owner can offer.
        """
        with self._lock:
            if person_id is None:
                cursor = self._conn.execute("DELETE FROM visit")
            else:
                cursor = self._conn.execute("DELETE FROM visit WHERE person_id=?", (person_id,))
            self._conn.commit()
            # Zero the freed pages, same reasoning as unenroll (E-5).
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.commit()
        logger.info("visits_purged", extra={"person_id": person_id, "deleted": cursor.rowcount})
        return cursor.rowcount

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

    def has_open_invite(self) -> bool:
        """Is any invite still usable? Cheap COUNT, called on every relay tick.

        The gate for relay polling (ADR-0038): with no open invite, nobody can
        submit a bundle, so a pickup poll cannot return anything. Deliberately a
        COUNT rather than reusing open_invite_registrations(), which builds full
        rows including hashes for republication.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM relay_invite "
                "WHERE consumed_at IS NULL AND revoked_at IS NULL AND expires_at > ? "
                "LIMIT 1",
                (datetime.now(UTC).isoformat(),),
            ).fetchone()
        return row is not None

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
                "SELECT p.person_id, p.display_name, pr.profile_id, pr.color, pr.sound, "
                "       p.consent_version, pr.accent_color "
                "FROM person p JOIN profile pr ON pr.person_id = p.person_id"
            ).fetchall()
            result: list[EnrolledPerson] = []
            for (
                person_id,
                display_name,
                profile_id,
                color,
                sound,
                consent_version,
                accent_color,
            ) in people:
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
                        accent_color=accent_color,
                        vectors=tuple(vectors),
                        consent_version=consent_version,
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

    @staticmethod
    def normalize_display_name(name: str) -> str:
        """Fold a display name for collision comparison (ADR-0019 §2).

        Case-insensitive and whitespace-collapsed, so "mom", "Mom" and "  Mom "
        are one name. Deliberately not stored -- only compared -- so the name the
        person chose is the name that appears on the wallboard.
        """
        return " ".join(name.split()).casefold()

    def _display_name_taken_locked(self, name: str) -> bool:
        """True when someone enrolled already answers to *name*. Caller holds ``_lock``.

        Without this a stranger could enrol as a resident's name and be greeted -- and
        announced to the owner over Telegram -- as them. Recognition grants no authority
        (ADR-0005 §3), but it does speak in the owner's voice, and that is worth
        protecting. No public variant: ``_lock`` is not reentrant, so a version that
        takes the lock would deadlock the one caller that matters, inside ``enroll``.
        """
        target = self.normalize_display_name(name)
        rows = self._conn.execute("SELECT display_name FROM person").fetchall()
        return any(self.normalize_display_name(r[0]) == target for r in rows)

    def count_consumed_invites(self, *, label: str) -> int:
        """How many invites carrying *label* actually produced an enrollment.

        With ``label`` set to the self-service marker this is "how many people added
        themselves", which is the number the owner wants to be able to see (ADR-0019
        §4). Derived rather than counted into new state: the invite row already
        records its own outcome.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM relay_invite WHERE label = ? AND consumed_at IS NOT NULL",
                (label,),
            ).fetchone()
        return int(row[0]) if row else 0

    def count_invites_since(self, since: datetime, *, label: str) -> int:
        """How many invites carrying *label* were minted at or after *since*.

        Counted from the table rather than an in-process tally so the self-service
        rate cap survives a door-visiond restart -- otherwise a restart would hand
        out a fresh allowance. Closed and consumed invites still count: the cap is
        on minting, not on outcomes.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM relay_invite WHERE label = ? AND created_at >= ?",
                (label, since.isoformat()),
            ).fetchone()
        return int(row[0]) if row else 0

    def list_people(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.person_id, p.display_name, p.consent_version, p.consent_at, "
                "       p.created_at, pr.profile_id, pr.color, pr.sound, pr.accent_color "
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
                    "accent_color": r[8] or r[6],
                }
                for r in rows
            ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
