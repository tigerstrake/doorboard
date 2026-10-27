"""The real Stanford dining food provider.

Implements the public ``FoodRecommendationProvider`` interface by running the
scrape -> score -> (optional LLM) pipeline for the current campus meal and
mapping the rich internal result down to the compact ``Recommendation`` the
wallboard food tile renders. On any data failure it raises ``RuntimeError`` so
the wallboard-worker job falls back to yesterday's cached pick.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime

import httpx
from pydantic import BaseModel, Field

from ..provider import FoodRecommendationProvider, Recommendation
from .meal import LA_TZ, meal_candidates, page_date
from .models import DiningHallMenu, DiningRecommendation
from .preferences import load_preferences
from .recommender import get_recommendation
from .scorer import score_all_halls
from .scraper import StanfordMenuScraper

logger = logging.getLogger(__name__)


class StanfordDiningConfig(BaseModel):
    """Configuration for :class:`StanfordDiningProvider` (secrets sourced from env)."""

    timeout_s: float = 15.0
    request_delay_s: float = 0.5
    max_response_bytes: int = 4_000_000
    hall_ids: list[str] = Field(default_factory=list)
    meal_override: str | None = None
    preferences_path: str | None = None
    use_ai: bool = False
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_s: float = 20.0


class StanfordDiningProvider(FoodRecommendationProvider):
    def __init__(
        self,
        config: StanfordDiningConfig | None = None,
        *,
        now: Callable[[], datetime] | None = None,
        http_transport: httpx.BaseTransport | None = None,
        openai_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or StanfordDiningConfig()
        self._now = now or (lambda: datetime.now(UTC))
        self._http_transport = http_transport
        self._openai_transport = openai_transport

    def get_daily_recommendation(self) -> Recommendation:
        now_la = self._now().astimezone(LA_TZ)
        target_date = now_la.date()
        candidates = meal_candidates(self.config.meal_override, now_la)

        scraper = StanfordMenuScraper(
            timeout_s=self.config.timeout_s,
            request_delay_s=self.config.request_delay_s,
            max_response_bytes=self.config.max_response_bytes,
            transport=self._http_transport,
        )
        meal, halls = self._scrape_first_stocked(scraper, target_date, candidates)

        prefs = load_preferences(self.config.preferences_path)
        scored = score_all_halls(halls, prefs)
        internal = get_recommendation(
            halls,
            scored,
            date=target_date.isoformat(),
            meal=meal,
            prefs=prefs,
            use_ai=self.config.use_ai,
            api_key=self.config.openai_api_key,
            model=self.config.openai_model,
            base_url=self.config.openai_base_url,
            timeout_s=self.config.openai_timeout_s,
            transport=self._openai_transport,
        )
        return self._to_public(internal, target_date)

    def _scrape_first_stocked(
        self, scraper: StanfordMenuScraper, day: date, candidates: list[str]
    ) -> tuple[str, list[DiningHallMenu]]:
        """Scrape each candidate meal in order; return the first with any items."""
        for meal in candidates:
            halls = scraper.scrape_all(
                date_str=page_date(day),
                meal=meal,
                hall_ids=self.config.hall_ids or None,
            )
            if any(h.items for h in halls):
                if meal != candidates[0]:
                    logger.warning(
                        "No %s menu for %s; using %s instead",
                        candidates[0],
                        day.isoformat(),
                        meal,
                    )
                return meal, halls
        raise RuntimeError(
            f"no menu items scraped for {day.isoformat()} / {'/'.join(candidates)} "
            "(menu may not be posted yet)"
        )

    @staticmethod
    def _to_public(internal: DiningRecommendation, day: date) -> Recommendation:
        if not internal.best_hall or internal.best_hall == "None":
            raise RuntimeError("no dining hall could be recommended")

        parts: list[str] = []
        plate = ", ".join(internal.recommended_plate[:3])
        if plate:
            parts.append(f"Try: {plate}")
        parts.append(f"{internal.confidence} confidence")
        if internal.backup_hall and internal.backup_hall != internal.best_hall:
            parts.append(f"backup: {internal.backup_hall}")

        return Recommendation(
            date=day,
            title=f"{internal.best_hall} — {internal.meal}",
            detail=" · ".join(parts),
            provider="stanford-ai" if internal.ai_generated else "stanford",
        )
