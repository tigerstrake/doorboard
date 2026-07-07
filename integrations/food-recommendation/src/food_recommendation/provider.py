from __future__ import annotations

import abc
from datetime import date
from typing import Literal
from pydantic import BaseModel, Field


class Recommendation(BaseModel):
    date: date
    title: str
    detail: str | None = None
    provider: str


class FoodRecommendationProvider(abc.ABC):
    @abc.abstractmethod
    def get_daily_recommendation(self, dt: date) -> Recommendation:
        """Fetch the daily food recommendation for the given date."""
        pass


FIXTURES = [
    {"title": "Spicy Tofu Soup", "detail": "From Soondubu house down the street"},
    {"title": "Double Cheeseburger", "detail": "In-N-Out Animal Style"},
    {"title": "Margherita Pizza", "detail": "Wood-fired with fresh basil"},
    {"title": "Chicken Tikka Masala", "detail": "With garlic naan"},
    {"title": "Sushi Deluxe Combo", "detail": "Chef's selection of fresh nigiri"},
    {"title": "Falafel Wrap", "detail": "With extra tahini and pickles"},
    {"title": "Tacos al Pastor", "detail": "From the local street food truck"},
]


class MockFoodRecommendationProvider(FoodRecommendationProvider):
    def get_daily_recommendation(self, dt: date) -> Recommendation:
        idx = dt.toordinal() % len(FIXTURES)
        item = FIXTURES[idx]
        return Recommendation(
            date=dt,
            title=item["title"],
            detail=item["detail"],
            provider="mock",
        )
