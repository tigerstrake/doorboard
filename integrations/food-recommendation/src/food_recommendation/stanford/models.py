"""Internal data models for the Stanford dining pipeline.

These are private to the ``stanford`` provider. They describe scraped menus and
scored halls; the provider maps the final :class:`DiningRecommendation` down to
the public ``food_recommendation.Recommendation`` the wallboard tile consumes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MenuItem(BaseModel):
    name: str
    ingredients: str = ""
    allergens: str = ""
    trace_allergens: str = ""
    is_gluten_free: bool = False
    is_vegetarian: bool = False
    is_vegan: bool = False
    is_halal: bool = False
    is_kosher: bool = False


class DiningHallMenu(BaseModel):
    hall_name: str
    hall_id: str
    date: str
    meal: str
    items: list[MenuItem] = Field(default_factory=list)


class ItemScore(BaseModel):
    name: str
    score: float
    tags: list[str] = Field(default_factory=list)


class ScoredHall(BaseModel):
    hall_name: str
    hall_id: str
    total_score: float
    breakdown: dict[str, dict[str, int]] = Field(default_factory=dict)
    top_items: list[ItemScore] = Field(default_factory=list)
    avoid_items: list[ItemScore] = Field(default_factory=list)
    item_count: int = 0


class DiningRecommendation(BaseModel):
    best_hall: str
    backup_hall: str
    recommended_plate: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    confidence: str
    reasoning: str
    ai_generated: bool = False
    date: str
    meal: str
