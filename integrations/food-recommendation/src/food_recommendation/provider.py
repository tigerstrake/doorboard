from __future__ import annotations

import abc
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

logger = logging.getLogger("doorboard.food_recommendation")


class Recommendation(BaseModel):
    date: date
    title: str
    detail: str | None = None
    provider: str


class FoodRecommendationProvider(abc.ABC):
    @abc.abstractmethod
    def get_daily_recommendation(self) -> Recommendation:
        """Return the provider's single recommendation for the current day."""
        pass


@dataclass(frozen=True)
class FoodFixture:
    title: str
    detail: str | None = None


class MockFoodRecommendationProvider(FoodRecommendationProvider):
    """Deterministic fixture rotation for dev, CI, and demos."""

    DEFAULT_FIXTURES: tuple[FoodFixture, ...] = (
        FoodFixture("Miso ramen", "Brothy, warm, and easy to make vegetarian."),
        FoodFixture("Paneer tikka bowl", "Rice, cucumber, mint yogurt, and roasted vegetables."),
        FoodFixture("Tomato egg noodles", "Fast pantry meal with scallions and chili crisp."),
        FoodFixture("Falafel pita", "Add pickles, tahini, and chopped salad."),
        FoodFixture("Black bean tacos", "Use cabbage slaw and lime crema."),
        FoodFixture("Kimchi fried rice", "Top with egg or tofu."),
        FoodFixture("Margherita flatbread", "Good with a side salad."),
    )

    def __init__(
        self,
        today: Callable[[], date] | None = None,
        fixtures: Sequence[FoodFixture] | None = None,
    ) -> None:
        self._today = today or (lambda: datetime.now(UTC).date())
        self._fixtures = tuple(fixtures or self.DEFAULT_FIXTURES)
        if not self._fixtures:
            raise ValueError("MockFoodRecommendationProvider requires at least one fixture")

    def get_daily_recommendation(self) -> Recommendation:
        current_date = self._today()
        fixture = self._fixtures[current_date.toordinal() % len(self._fixtures)]
        return Recommendation(
            date=current_date,
            title=fixture.title,
            detail=fixture.detail,
            provider="mock",
        )


class FoodRecommendationCache:
    """Single-record JSON cache used for yesterday fallback across worker restarts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> Recommendation | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return Recommendation.model_validate(raw)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError, ValidationError, TypeError) as exc:
            logger.warning("Ignoring invalid food recommendation cache %s: %s", self.path, exc)
            return None

    def load_for_date(self, recommendation_date: date) -> Recommendation | None:
        recommendation = self.load()
        if recommendation is None or recommendation.date != recommendation_date:
            return None
        return recommendation

    def save(self, recommendation: Recommendation) -> None:
        payload: dict[str, Any] = recommendation.model_dump(mode="json")
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            tmp_path.replace(self.path)
        except OSError as exc:
            logger.warning("Failed to update food recommendation cache %s: %s", self.path, exc)
