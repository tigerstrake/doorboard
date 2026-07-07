"""In-memory nearest-neighbour matcher (ADR-0009 §2, mechanism E-1).

Enrolled vectors are held only in memory, loaded once and refreshed on
enroll/unenroll.  A query embedding is matched and then dropped by the caller —
there is deliberately no collection keyed by an *unknown* identity here (no
"recent unknowns", no ring buffer, no clustering).  Adding one is a
review-blocking defect.

Matching is cosine similarity in pure Python (no numpy dependency); the
enrolled set is small (a household), so this is trivially fast.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from door_visiond.embedding import Embedding
from door_visiond.enrollment import EnrolledPerson


@dataclass(frozen=True)
class MatchResult:
    person_id: str
    display_name: str
    profile_id: str
    color: str
    sound: str | None
    score: float


@dataclass(frozen=True)
class _EnrolledVector:
    person_id: str
    display_name: str
    profile_id: str
    color: str
    sound: str | None
    unit: tuple[float, ...]  # L2-normalized enrolled vector


def _normalize(values: tuple[float, ...]) -> tuple[float, ...] | None:
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0.0:
        return None
    return tuple(v / norm for v in values)


class Matcher:
    def __init__(self, threshold: float) -> None:
        self._threshold = threshold
        self._vectors: list[_EnrolledVector] = []

    @property
    def enrolled_count(self) -> int:
        # Distinct persons currently loaded.
        return len({v.person_id for v in self._vectors})

    def refresh(self, enrolled: list[EnrolledPerson]) -> None:
        """Reload the in-memory enrolled set (called after enroll/unenroll)."""
        vectors: list[_EnrolledVector] = []
        for person in enrolled:
            for emb in person.vectors:
                unit = _normalize(emb.expose_for_matching())
                if unit is None:
                    continue
                vectors.append(
                    _EnrolledVector(
                        person_id=person.person_id,
                        display_name=person.display_name,
                        profile_id=person.profile_id,
                        color=person.color,
                        sound=person.sound,
                        unit=unit,
                    )
                )
        self._vectors = vectors

    def match(self, embedding: Embedding) -> MatchResult | None:
        """Return the best enrolled match at or above threshold, else None."""
        if not self._vectors:
            return None
        query = _normalize(embedding.expose_for_matching())
        if query is None:
            return None

        best: _EnrolledVector | None = None
        best_score = -1.0
        for candidate in self._vectors:
            if len(candidate.unit) != len(query):
                continue
            score = sum(a * b for a, b in zip(candidate.unit, query, strict=True))
            if score > best_score:
                best_score = score
                best = candidate

        if best is None or best_score < self._threshold:
            return None
        return MatchResult(
            person_id=best.person_id,
            display_name=best.display_name,
            profile_id=best.profile_id,
            color=best.color,
            sound=best.sound,
            score=best_score,
        )
