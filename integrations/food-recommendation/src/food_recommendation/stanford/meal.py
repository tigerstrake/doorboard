"""Meal-window detection for Stanford dining.

Uses the campus timezone (America/Los_Angeles via stdlib ``zoneinfo``) to pick
the meal that matches the current local time. Weekends have a single daytime
sitting, which the menu site publishes under "Lunch" (see ``detect_meal``).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

LA_TZ = ZoneInfo("America/Los_Angeles")

VALID_MEALS = ("Breakfast", "Lunch", "Brunch", "Dinner")

# Meals to try, in order, when the auto-detected meal has no menu at all. Only
# used for auto detection; an explicitly configured meal is never swapped.
MEAL_FALLBACKS: dict[str, tuple[str, ...]] = {
    "Brunch": ("Lunch", "Breakfast"),
    "Lunch": ("Brunch",),
    "Breakfast": ("Brunch",),
    "Dinner": (),
}


def detect_meal(now_la: datetime) -> str:
    """Return the meal name for the given local (LA) datetime."""
    hour = now_la.hour
    if now_la.weekday() >= 5:  # 5=Sat, 6=Sun
        # The hours page calls the weekend daytime sitting "Brunch/Lunch", but
        # the menu site publishes it under "Lunch"; its "Brunch" option is
        # empty on every date, so asking for Brunch always scraped nothing.
        return "Lunch" if hour < 15 else "Dinner"
    if 6 <= hour < 10:
        return "Breakfast"
    if 10 <= hour < 15:
        return "Lunch"
    if 15 <= hour < 21:
        return "Dinner"
    # Outside defined windows — pick the nearest sensible meal.
    if hour < 6:
        return "Breakfast"
    return "Dinner"


def resolve_meal(meal: str | None, now_la: datetime) -> str:
    """Resolve an explicit meal name, or auto-detect when ``meal`` is falsy."""
    if not meal or meal.lower() == "auto":
        return detect_meal(now_la)
    normalized = {m.lower(): m for m in VALID_MEALS}.get(meal.lower())
    if not normalized:
        raise ValueError(f"Unknown meal {meal!r}. Use one of: auto, {', '.join(VALID_MEALS)}")
    return normalized


def meal_candidates(meal: str | None, now_la: datetime) -> list[str]:
    """Meals to scrape, in order: the resolved meal, then its fallbacks if auto."""
    resolved = resolve_meal(meal, now_la)
    if not meal or meal.lower() == "auto":
        return [resolved, *MEAL_FALLBACKS.get(resolved, ())]
    return [resolved]


def page_date(d: date) -> str:
    """Format a date the way the menu page's day dropdown expects (M/D/YYYY)."""
    return f"{d.month}/{d.day}/{d.year}"
