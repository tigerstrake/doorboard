"""Real Stanford R&DE dining-hall food recommendation provider.

Scrapes the Stanford dining menus, scores them against bundled dietary
preferences, optionally refines the pick with an LLM, and exposes the result
through the shared ``FoodRecommendationProvider`` interface.
"""

from food_recommendation.stanford.provider import (
    StanfordDiningConfig,
    StanfordDiningProvider,
)

__all__ = ["StanfordDiningConfig", "StanfordDiningProvider"]
