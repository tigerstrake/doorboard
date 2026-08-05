"""Enrollment store: matching round-trip and ADR-0009 P-5 (deletion purges bytes)."""

from __future__ import annotations

from dataclasses import astuple
from datetime import UTC, datetime
from pathlib import Path

import pytest
from door_visiond.embedder import MockEmbedder, sentinel_embedding
from door_visiond.enrollment import EnrollmentStore, ProfileSpec
from door_visiond.matcher import Matcher
from door_visiond.settings import Settings

from .conftest import CONSENT_VERSION, TEST_DIM, sentinel


def _sidecar_bytes(db_path: Path) -> bytes:
    blob = b""
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            blob += p.read_bytes()
    return blob


def test_enroll_load_and_match_roundtrip(ssd_settings: Settings) -> None:
    store = EnrollmentStore(ssd_settings.enrollment_db_path)
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(b"alex")
    person_id = store.enroll(
        display_name="Alex",
        consent_version=CONSENT_VERSION,
        consent_at=datetime.now(UTC),
        embeddings=[(emb, "mock", 0.9)],
        profile=ProfileSpec(profile_id="blue_wave", color="#0000ff", sound="chime"),
    )

    matcher = Matcher(ssd_settings.match_threshold)
    matcher.refresh(store.load_enrolled())

    result = matcher.match(emb)
    assert result is not None
    assert result.person_id == person_id
    assert result.display_name == "Alex"
    assert result.profile_id == "blue_wave"

    # A different face does not match.
    other, _q2 = MockEmbedder(dim=TEST_DIM).embed(b"stranger")
    assert matcher.match(other) is None
    store.close()


def test_unenroll_purges_bytes(ssd_settings: Settings) -> None:
    """P-5: unenroll leaves no sentinel bytes in the DB or its WAL/SHM."""
    store = EnrollmentStore(ssd_settings.enrollment_db_path)
    marker = sentinel("P5")
    emb = sentinel_embedding(marker, TEST_DIM)
    person_id = store.enroll(
        display_name="Alex",
        consent_version=CONSENT_VERSION,
        consent_at=datetime.now(UTC),
        embeddings=[(emb, "mock", 0.9)],
        profile=ProfileSpec(profile_id="blue_wave", color="#0000ff", sound=None),
    )

    # Sanity: the marker really is stored somewhere before deletion.
    assert marker in _sidecar_bytes(ssd_settings.enrollment_db_path)

    assert store.unenroll(person_id) is True

    # Proven by inspection: no marker survives in the main DB or its sidecars.
    assert marker not in _sidecar_bytes(ssd_settings.enrollment_db_path)
    assert store.person_count() == 0
    assert store.embedding_count() == 0

    # The tombstone is opaque: id + timestamps, and crucially no display name.
    tomb = store.get_tombstone(person_id)
    assert tomb is not None
    assert tomb.person_id == person_id
    assert "Alex" not in "".join(str(v) for v in astuple(tomb))
    store.close()


def test_unenroll_missing_person_is_noop_with_tombstone(ssd_settings: Settings) -> None:
    store = EnrollmentStore(ssd_settings.enrollment_db_path)
    assert store.unenroll("prs_doesnotexist") is False
    store.close()


# -- profile allocation (two people wanting the same colour) ----------------


def test_a_taken_colour_is_reassigned_not_rejected(ssd_settings: Settings) -> None:
    """Regression: this failed with a bare IntegrityError in production.

    `profile.profile_id` is UNIQUE (ADR-0009 §1) so each person gets a
    distinguishable light. Two people picking the same colour is ordinary, though,
    and used to surface on the enrollee's phone as "Enrolment did not complete.
    Ask the household admin to check the doorboard" — with nothing in the logs
    naming the cause.
    """
    from door_visiond.enrollment import PROFILE_CATALOG

    store = EnrollmentStore(ssd_settings.enrollment_db_path)
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(b"anyone")

    first = store.enroll(
        display_name="Tiger",
        consent_version=CONSENT_VERSION,
        consent_at=datetime.now(UTC),
        embeddings=[(emb, "mock", 0.9)],
        profile=ProfileSpec(profile_id="blue_wave", color="#3a86ff"),
    )
    assert store.last_assigned_profile == "blue_wave"
    assert store.last_profile_was_reassigned is False

    second = store.enroll(
        display_name="Dad",
        consent_version=CONSENT_VERSION,
        consent_at=datetime.now(UTC),
        embeddings=[(emb, "mock", 0.9)],
        profile=ProfileSpec(profile_id="blue_wave", color="#3a86ff"),
    )
    assert second != first
    assert store.last_profile_was_reassigned is True
    assert store.last_assigned_profile != "blue_wave"
    # And it stays inside the catalogue, so the ESP32 gets an effect id it knows.
    assert store.last_assigned_profile in {pid for pid, _color in PROFILE_CATALOG}

    people = {p["display_name"]: p["profile_id"] for p in store.list_people()}
    assert people["Tiger"] == "blue_wave"
    assert people["Dad"] != people["Tiger"]


def test_the_whole_household_can_enrol_on_one_colour_choice(ssd_settings: Settings) -> None:
    from door_visiond.enrollment import PROFILE_CATALOG, NoProfileAvailableError

    store = EnrollmentStore(ssd_settings.enrollment_db_path)
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(b"anyone")

    for index in range(len(PROFILE_CATALOG)):
        store.enroll(
            display_name=f"Person {index}",
            consent_version=CONSENT_VERSION,
            consent_at=datetime.now(UTC),
            embeddings=[(emb, "mock", 0.9)],
            profile=ProfileSpec(profile_id="blue_wave", color="#3a86ff"),
        )

    assigned = {p["profile_id"] for p in store.list_people()}
    assert len(assigned) == len(PROFILE_CATALOG), "each person needs a distinct light"

    # One more than the catalogue holds is a clear, named failure -- not an
    # IntegrityError surfacing as "internal_error" on someone's phone.
    with pytest.raises(NoProfileAvailableError):
        store.enroll(
            display_name="One too many",
            consent_version=CONSENT_VERSION,
            consent_at=datetime.now(UTC),
            embeddings=[(emb, "mock", 0.9)],
            profile=ProfileSpec(profile_id="blue_wave", color="#3a86ff"),
        )
