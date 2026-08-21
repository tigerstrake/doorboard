"""Arriving with someone else must not stop you being recognised.

The bug: `_match_primary_face` compared only the *largest* qualifying face. Walk up with a
friend standing slightly closer to the camera and your face was detected, never compared, and
the door greeted nobody — with nothing in the logs to say why, because no match was ever
attempted. On a door people approach in pairs that is routine, not an edge case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from door_visiond.embedding import Embedding
from door_visiond.pipeline import DetectedFace, FrameCapture, PipelineCore

_DIM = 8


@dataclass(frozen=True)
class _Match:
    person_id: str
    display_name: str
    profile_id: str | None
    color: str | None
    sound: str | None
    score: float
    consent_version: str | None = "v3"
    accent_color: str | None = None


class _MatcherByFirstElement:
    """Matches a face by the first element of its vector, so tests can aim faces."""

    def __init__(self, table: dict[float, _Match]) -> None:
        self.table = table
        self.calls: list[float] = []

    def match(self, embedding: Embedding) -> _Match | None:
        key = round(embedding.expose_for_matching()[0], 3)
        self.calls.append(key)
        return self.table.get(key)


def _face(marker: float, size_px: int) -> DetectedFace:
    return DetectedFace(
        size_px=size_px,
        embedding=Embedding(tuple([marker] + [0.0] * (_DIM - 1))),
        quality=0.9,
    )


def _core(matcher: Any, **kwargs: Any) -> PipelineCore:
    from door_visiond.clock import SystemClock
    from door_visiond.identity_cache import IdentityCache

    defaults: dict[str, Any] = dict(
        clock=SystemClock(),
        door_id="primary",
        matcher=matcher,
        cache=IdentityCache(),
        sink=lambda _event: None,
        min_face_px=80,
        ttl_ms=2500,
        cooldown_ms=30_000,
        stability_window=3,
        stability_required=2,
    )
    defaults.update(kwargs)
    return PipelineCore(**defaults)


def test_an_enrolled_person_is_found_behind_a_larger_unknown_face() -> None:
    tiger = _Match("prs_tiger", "Tiger", "warm_amber", None, None, 0.71)
    matcher = _MatcherByFirstElement({0.2: tiger})
    core = _core(matcher)

    # The stranger is closer to the camera, so their face is bigger.
    capture = FrameCapture(
        faces=(_face(0.9, 200), _face(0.2, 120)),
        inference_ms=5.0,
    )
    result = core._match_primary_face(capture, now=1000)

    assert result is not None, "the enrolled face was never compared"
    assert result.person_id == "prs_tiger"
    assert 0.9 in matcher.calls and 0.2 in matcher.calls, "both faces must be tried"


def test_the_better_match_wins_not_the_bigger_face() -> None:
    weak = _Match("prs_adam", "Adam", None, None, None, 0.64)
    strong = _Match("prs_tiger", "Tiger", None, None, None, 0.88)
    core = _core(_MatcherByFirstElement({0.5: weak, 0.6: strong}))

    capture = FrameCapture(faces=(_face(0.5, 240), _face(0.6, 100)), inference_ms=5.0)
    result = core._match_primary_face(capture, now=1000)

    assert result is not None
    assert result.person_id == "prs_tiger", "confidence should decide, not proximity"


def test_faces_below_the_size_gate_are_still_ignored() -> None:
    tiger = _Match("prs_tiger", "Tiger", None, None, None, 0.9)
    matcher = _MatcherByFirstElement({0.2: tiger})
    core = _core(matcher)

    capture = FrameCapture(faces=(_face(0.2, 20),), inference_ms=5.0)
    assert core._match_primary_face(capture, now=1000) is None
    assert matcher.calls == [], "a face too small to trust must not even be compared"


def test_the_number_of_faces_compared_per_frame_is_bounded() -> None:
    # A doorway crowd should cost a bounded amount of work; the people at the back are not
    # the ones ringing the bell.
    matcher = _MatcherByFirstElement({})
    core = _core(matcher, max_faces_matched=3)

    faces = tuple(_face(round(0.1 * i, 3), 100 + i) for i in range(1, 9))
    core._match_primary_face(FrameCapture(faces=faces, inference_ms=5.0), now=1000)

    assert len(matcher.calls) == 3
    # And it keeps the biggest ones, not an arbitrary three.
    assert matcher.calls == [0.8, 0.7, 0.6]


def test_no_qualifying_faces_is_not_an_error() -> None:
    core = _core(_MatcherByFirstElement({}))
    assert core._match_primary_face(FrameCapture(faces=(), inference_ms=5.0), now=1000) is None
