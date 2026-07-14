"""Meal-window detection for Stanford dining.

Uses the campus timezone (America/Los_Angeles via stdlib ``zoneinfo``) to pick
the meal that matches the current local time. Weekends collapse breakfast/lunch
into brunch, matching Stanford R&DE's dining schedule.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

LA_TZ = ZoneInfo("America/Los_Angeles")

VALID_MEALS = ("Breakfast", "Lunch", "Brunch", "Dinner")


def detect_meal(now_la: datetime) -> str:
    """Return the meal name for the given local (LA) datetime."""
    hour = now_la.hour
    is_weekend = now_la.weekday() >= 5  # 5=Sat, 6=Sun

    if 6 <= hour < 10:
        return "Brunch" if is_weekend else "Breakfast"
    if 10 <= hour < 15:
        return "Brunch" if is_weekend else "Lunch"
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


def page_date(d: date) -> str:
    """Format a date the way the menu page's day dropdown expects (M/D/YYYY)."""
    return f"{d.month}/{d.day}/{d.year}"
