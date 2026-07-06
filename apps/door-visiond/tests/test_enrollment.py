"""Enrollment store: matching round-trip and ADR-0009 P-5 (deletion purges bytes)."""

from __future__ import annotations

from dataclasses import astuple
from datetime import UTC, datetime
from pathlib import Path

from door_visiond.embedder import MockEmbedder, sentinel_embedding
from door_visiond.enrollment import EnrollmentStore, ProfileSpec
from door_visiond.matcher import Matcher
from door_visiond.settings import Settings

from .conftest import TEST_DIM, sentinel


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
        consent_version="v1",
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
        consent_version="v1",
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
