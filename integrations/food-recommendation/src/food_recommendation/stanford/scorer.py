"""Deterministic, preference-driven scoring of scraped dining-hall menus.

Each menu item is matched (case-insensitively, name + ingredients) against the
keyword rules in :mod:`preferences`; bonuses add and penalties subtract. Halls
are ranked by total score. This deterministic result is both a standalone
recommendation and the strong signal handed to the optional LLM step.
"""

from __future__ import annotations

import re
from typing import Any

from .models import DiningHallMenu, DiningRecommendation, ItemScore, MenuItem, ScoredHall
from .preferences import load_preferences

# (category, score_delta, keywords)
Rule = tuple[str, float, list[str]]


def _build_rules(prefs: dict[str, Any]) -> tuple[list[Rule], list[Rule]]:
    scoring = prefs["scoring"]
    bonuses: list[Rule] = [
        (r["category"], float(r["score"]), [kw.lower() for kw in r["keywords"]])
        for r in scoring["bonuses"]
    ]
    penalties: list[Rule] = [
        (r["category"], float(r["score"]), [kw.lower() for kw in r["keywords"]])
        for r in scoring["penalties"]
    ]
    return bonuses, penalties


def _match_keywords(text: str, keywords: list[str]) -> bool:
    # Allow a trailing plural 's' so "lentil" matches "lentils".
    return any(re.search(r"\b" + re.escape(kw) + r"s?\b", text) for kw in keywords)


def _score_item(
    item: MenuItem, bonuses: list[Rule], penalties: list[Rule]
) -> tuple[float, list[str]]:
    text = f"{item.name} {item.ingredients}".lower()
    total = 0.0
    tags: list[str] = []
    for category, delta, keywords in bonuses:
        if _match_keywords(text, keywords):
            total += delta
            tags.append(f"+{category}")
    for category, delta, keywords in penalties:
        if _match_keywords(text, keywords):
            total += delta
            tags.append(f"-{category}")
    return total, tags


def score_item(item: MenuItem, prefs: dict[str, Any] | None = None) -> tuple[float, list[str]]:
    bonuses, penalties = _build_rules(prefs or load_preferences())
    return _score_item(item, bonuses, penalties)


def _score_hall(menu: DiningHallMenu, bonuses: list[Rule], penalties: list[Rule]) -> ScoredHall:
    if not menu.items:
        return ScoredHall(hall_name=menu.hall_name, hall_id=menu.hall_id, total_score=0.0)

    positive_hits: dict[str, int] = {}
    penalty_hits: dict[str, int] = {}
    scored_items: list[ItemScore] = []
    for item in menu.items:
        raw_score, tags = _score_item(item, bonuses, penalties)
        scored_items.append(ItemScore(name=item.name, score=raw_score, tags=tags))
        for tag in tags:
            bucket = positive_hits if tag.startswith("+") else penalty_hits
            key = tag[1:]
            bucket[key] = bucket.get(key, 0) + 1

    scored_items.sort(key=lambda x: x.score, reverse=True)
    return ScoredHall(
        hall_name=menu.hall_name,
        hall_id=menu.hall_id,
        total_score=round(sum(it.score for it in scored_items), 2),
        breakdown={"positive_hits": positive_hits, "penalty_hits": penalty_hits},
        top_items=[it for it in scored_items if it.score > 0][:8],
        avoid_items=[it for it in scored_items if it.score < 0],
        item_count=len(menu.items),
    )


def score_hall(menu: DiningHallMenu, prefs: dict[str, Any] | None = None) -> ScoredHall:
    bonuses, penalties = _build_rules(prefs or load_preferences())
    return _score_hall(menu, bonuses, penalties)


def score_all_halls(
    menus: list[DiningHallMenu], prefs: dict[str, Any] | None = None
) -> list[ScoredHall]:
    bonuses, penalties = _build_rules(prefs or load_preferences())
    scored = [_score_hall(m, bonuses, penalties) for m in menus]
    scored.sort(key=lambda s: s.total_score, reverse=True)
    return scored


def build_deterministic_recommendation(
    scored: list[ScoredHall], date: str, meal: str
) -> DiningRecommendation:
    halls_with_items = [s for s in scored if s.item_count > 0]
    if not halls_with_items:
        return DiningRecommendation(
            best_hall="None",
            backup_hall="None",
            confidence="low",
            reasoning="No menu data available for any dining hall.",
            ai_generated=False,
            date=date,
            meal=meal,
        )

    best = halls_with_items[0]
    backup = halls_with_items[1] if len(halls_with_items) > 1 else best

    confidence = "high" if best.total_score > 10 else ("medium" if best.total_score > 3 else "low")
    top_tags = sorted(
        best.breakdown.get("positive_hits", {}).items(), key=lambda x: x[1], reverse=True
    )
    tag_summary = ", ".join(t.replace("_", " ") for t, _ in top_tags[:3]) or "varied options"

    return DiningRecommendation(
        best_hall=best.hall_name,
        backup_hall=backup.hall_name,
        recommended_plate=[it.name for it in best.top_items[:5]],
        avoid=[it.name for it in best.avoid_items[:3]],
        confidence=confidence,
        reasoning=(
            f"{best.hall_name} scores highest ({best.total_score:.1f} pts) with strong "
            f"offerings in: {tag_summary}. Backup: {backup.hall_name} "
            f"({backup.total_score:.1f} pts)."
        ),
        ai_generated=False,
        date=date,
        meal=meal,
    )
