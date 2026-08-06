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

import logging
import math
from dataclasses import dataclass

from door_visiond.embedding import Embedding
from door_visiond.enrollment import EnrolledPerson

logger = logging.getLogger("door_visiond.matcher")


@dataclass(frozen=True)
class MatchResult:
    person_id: str
    display_name: str
    profile_id: str
    color: str
    sound: str | None
    score: float
    consent_version: str = ""


@dataclass(frozen=True)
class _EnrolledVector:
    person_id: str
    display_name: str
    profile_id: str
    color: str
    sound: str | None
    consent_version: str
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
                        consent_version=person.consent_version,
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
        comparable = 0
        for candidate in self._vectors:
            if len(candidate.unit) != len(query):
                # A template from a different model dimension can never match. It
                # was skipped silently, which would present as "recognition simply
                # does not work" with no way to tell from the outside.
                continue
            comparable += 1
            score = sum(a * b for a, b in zip(candidate.unit, query, strict=True))
            if score > best_score:
                best_score = score
                best = candidate

        if best is None or best_score < self._threshold:
            # Log the near miss. A failed match was previously indistinguishable
            # from "no face was ever looked at": the door greeted nobody for a day
            # with an enrolled person in front of it, and nothing recorded whether
            # the score was 0.61 (raise-the-lid territory) or 0.03 (the templates
            # are unusable). Only the scalar score and the candidate's person_id go
            # out — never the embedding, which ADR-0009 E-3 forbids logging.
            logger.info(
                "match_below_threshold",
                extra={
                    "best_score": round(best_score, 4) if best is not None else None,
                    "threshold": self._threshold,
                    "candidates": len(self._vectors),
                    "comparable_candidates": comparable,
                    "best_person_id": best.person_id if best is not None else None,
                },
            )
            return None
        return MatchResult(
            person_id=best.person_id,
            display_name=best.display_name,
            profile_id=best.profile_id,
            color=best.color,
            sound=best.sound,
            score=best_score,
            consent_version=best.consent_version,
        )
