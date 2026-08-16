"""ARCHITECTURE.md §4 — what "face visible → stable identity" actually measures.

The budget is p95 < 600 ms and the only place it is measured on the real door is
``face_to_identity_ms`` here (the simulator harness cannot see a camera or the
Hailo, so it reports the path as N/A). These tests pin the *start* of that span to
the first frame a face appeared in, because the frames a visitor spends detected
but not yet recognised — walking up, too far away, mid-turn — are the bulk of what
they stand there waiting through.
"""

from __future__ import annotations

from door_visiond.embedder import MockEmbedder
from door_visiond.matcher import EnrolledPerson
from door_visiond.pipeline import FrameCapture

from .conftest import TEST_DIM, face

# conftest's Settings fixture leaves min_face_px at its default 80.
_TOO_SMALL_PX = 40
_BIG_ENOUGH_PX = 120


def _enrol(matcher, embedding) -> None:
    matcher.refresh(
        [
            EnrolledPerson(
                person_id="prs_alex",
                display_name="Alex",
                profile_id="blue_wave",
                color="#00f",
                sound=None,
                vectors=(embedding,),
            )
        ]
    )


def test_span_starts_when_the_face_appears_not_when_it_matches(core_env, clock) -> None:
    """Frames spent visible-but-unrecognised belong to the measured wait."""
    core, matcher, _cache = core_env
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(b"alex")
    _enrol(matcher, emb)

    # 300 ms approaching: a face is detected every frame, too small to match.
    for _ in range(3):
        core.process_capture(face(emb, size_px=_TOO_SMALL_PX))
        clock.advance(100)
    assert core.metrics_snapshot()["identity_stable_count"] == 0

    # Close enough now: two matching frames satisfy 2-of-3 and emit.
    core.process_capture(face(emb, size_px=_BIG_ENOUGH_PX))
    clock.advance(100)
    core.process_capture(face(emb, size_px=_BIG_ENOUGH_PX))

    snap = core.metrics_snapshot()
    assert snap["identity_stable_count"] == 1
    # 400 ms of standing there, not the 100 ms between the two matching frames.
    assert snap["face_to_identity_ms_p50"] == 400.0


def test_empty_doorway_restarts_the_span(core_env, clock) -> None:
    """An unrelated earlier visitor must not inflate the next person's wait."""
    core, matcher, _cache = core_env
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(b"alex")
    _enrol(matcher, emb)

    # Someone loiters at the edge of frame for 2 s, never close enough to match.
    for _ in range(20):
        core.process_capture(face(emb, size_px=_TOO_SMALL_PX))
        clock.advance(100)

    # Doorway empties.
    core.process_capture(FrameCapture(faces=(), inference_ms=5.0))
    clock.advance(100)

    # A new approach, recognised immediately on two frames.
    core.process_capture(face(emb, size_px=_BIG_ENOUGH_PX))
    clock.advance(100)
    core.process_capture(face(emb, size_px=_BIG_ENOUGH_PX))

    snap = core.metrics_snapshot()
    assert snap["identity_stable_count"] == 1
    # 100 ms: from the first frame of *this* approach, not the 2 s of loitering.
    assert snap["face_to_identity_ms_p50"] == 100.0
