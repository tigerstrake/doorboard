"""ADR-0009 P-1, P-2, P-4, P-10 — unknown-embedding privacy invariants.

These prove non-persistence and log-cleanliness by *inspection* (sentinel byte
scans), not by trust.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from door_visiond.embedder import MockEmbedder, sentinel_embedding
from door_visiond.enrollment import EnrollmentStore, ProfileSpec
from door_visiond.events import EventEmitter
from door_visiond.matcher import Matcher
from door_visiond.settings import Settings
from doorboard_observability.redaction import REDACTED

from .conftest import TEST_DIM, capture_logs, face, scan_tree_for, sentinel


def _enroll_known(store: EnrollmentStore, *, seed: bytes = b"alex") -> None:
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(seed)
    store.enroll(
        display_name="Alex",
        consent_version="v1",
        consent_at=datetime.now(UTC),
        embeddings=[(emb, "mock", 0.9)],
        profile=ProfileSpec(profile_id="blue_wave", color="#0000ff", sound=None),
    )


def _file_names(root) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def test_unknown_embedding_never_touches_disk(core_env, ssd_settings: Settings) -> None:
    """P-1: sentinel-bearing unknown embeddings leave no trace on the SSD."""
    core, matcher, _cache = core_env
    store = EnrollmentStore(ssd_settings.enrollment_db_path)
    _enroll_known(store)
    matcher.refresh(store.load_enrolled())

    people_before = store.person_count()
    embs_before = store.embedding_count()
    files_before = _file_names(ssd_settings.ssd_data_root)

    marker = sentinel("P1")
    for i in range(50):
        emb = sentinel_embedding(marker + i.to_bytes(4, "big"), TEST_DIM)
        core.process_capture(face(emb))

    # The marker must not appear anywhere under the SSD root (DB, WAL, tmp).
    assert scan_tree_for(ssd_settings.ssd_data_root, marker) == []
    # No rows added, no new files.
    assert store.person_count() == people_before
    assert store.embedding_count() == embs_before
    assert _file_names(ssd_settings.ssd_data_root) == files_before
    store.close()


def test_unknown_face_emits_face_visible_only(core_env, collected) -> None:
    """P-2: unknowns produce only vision.face_visible with contract-exact fields."""
    core, _matcher, _cache = core_env
    marker = sentinel("P2")
    for i in range(10):
        emb = sentinel_embedding(marker + i.to_bytes(4, "big"), TEST_DIM)
        core.process_capture(face(emb))

    assert collected, "expected face_visible events"
    assert all(e.type == "vision.face_visible" for e in collected)
    assert not any(e.type.startswith("vision.identity") for e in collected)
    for event in collected:
        # extra="forbid" on the payload already guarantees no vector field;
        # assert the fields are exactly the counts/pixel size.
        assert set(event.payload.model_dump().keys()) == {"face_count", "largest_face_px"}


def test_logs_are_biometric_clean(ssd_settings: Settings, clock, collected) -> None:
    """P-4: with the real emitter, no sentinel/vector reaches the logs."""
    from door_visiond.identity_cache import IdentityCache
    from door_visiond.pipeline import PipelineCore

    emitter = EventEmitter(ssd_settings.door_id)
    core = PipelineCore(
        matcher=Matcher(ssd_settings.match_threshold),
        cache=IdentityCache(),
        sink=emitter.emit,
        clock=clock,
        door_id="primary",
        min_face_px=ssd_settings.min_face_px,
        ttl_ms=ssd_settings.identity_cache_ttl_ms,
        cooldown_ms=ssd_settings.greeting_cooldown_ms,
        stability_window=ssd_settings.stability_window,
        stability_required=ssd_settings.stability_required,
    )
    marker = sentinel("P4")
    with capture_logs("door_visiond") as records:
        for i in range(20):
            emb = sentinel_embedding(marker + i.to_bytes(4, "big"), TEST_DIM)
            core.process_capture(face(emb))
        # also try to actively leak via a log call — must be scrubbed
        logging.getLogger("door_visiond.events").info(
            "leak_probe", extra={"embedding": [3.14159] * TEST_DIM}
        )

    assert records
    for record in records:
        rendered = record.getMessage() + repr(vars(record))
        assert marker not in rendered.encode("latin-1", "ignore")
        assert getattr(record, "embedding", REDACTED) == REDACTED
        for value in vars(record).values():
            if isinstance(value, (bytes, bytearray)):
                assert len(value) <= 64
            if isinstance(value, (list, tuple)) and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) for x in value
            ):
                assert len(value) <= 16


def test_unknown_faces_never_key_any_collection(core_env) -> None:
    """E-1: no structure is keyed by an unknown identity (no unbounded growth)."""
    core, _matcher, _cache = core_env
    marker = sentinel("EX")
    for i in range(200):
        emb = sentinel_embedding(marker + i.to_bytes(4, "big"), TEST_DIM)
        core.process_capture(face(emb))
    assert core._first_seen_ms == {}
    assert core._last_stable_ms == {}
    assert core._streak_trace == {}
    assert all(entry is None for entry in core._ring)


def test_cooldown_suppresses_reidentify(core_env, collected, clock) -> None:
    """P-10: a continuously-visible person yields one identity_stable per 30 s."""
    core, matcher, _cache = core_env
    store_emb, _q = MockEmbedder(dim=TEST_DIM).embed(b"alex")
    from door_visiond.matcher import EnrolledPerson

    matcher.refresh(
        [
            EnrolledPerson(
                person_id="prs_alex",
                display_name="Alex",
                profile_id="blue_wave",
                color="#00f",
                sound=None,
                vectors=(store_emb,),
            )
        ]
    )

    def stables() -> int:
        return sum(1 for e in collected if e.type == "vision.identity_stable")

    # Continuous presence for ~5 s (well within the 30 s cooldown window).
    for _ in range(50):
        core.process_capture(face(store_emb))
        clock.advance(100)
    assert stables() == 1

    # Advance past the cooldown; presence continues → exactly one more.
    clock.advance(31_000)
    for _ in range(5):
        core.process_capture(face(store_emb))
        clock.advance(100)
    assert stables() == 2
