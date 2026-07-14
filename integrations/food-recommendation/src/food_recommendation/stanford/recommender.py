"""Turn scored halls into a final recommendation.

Deterministic scoring is always available. When an OpenAI key is configured the
scored menus are sent to the Chat Completions API (via ``httpx`` — no ``openai``
SDK dependency) for a more natural pick; any failure falls back to the
deterministic result, so the LLM is a strict enhancement, never a hard dependency.

The API key is never logged and never placed in an exception message.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .models import DiningHallMenu, DiningRecommendation, ScoredHall
from .preferences import load_preferences
from .scorer import build_deterministic_recommendation

logger = logging.getLogger("doorboard.food_recommendation.stanford.recommender")

_REQUIRED_KEYS = {
    "best_hall",
    "backup_hall",
    "recommended_plate",
    "avoid",
    "confidence",
    "reasoning",
}


def _build_system_prompt(prefs: dict[str, Any]) -> str:
    notes = "\n".join(f"- {note}" for note in prefs["notes"])
    hard_avoids = [
        r["category"].replace("_", " ")
        for r in prefs["scoring"]["penalties"]
        if abs(float(r["score"])) >= 4.5
    ]
    hard_avoid_str = ", ".join(hard_avoids) if hard_avoids else "none specified"
    return (
        "You are a personal nutrition advisor for a Stanford student.\n\n"
        f"Goal: {prefs['description'].strip()}\n\n"
        f"Specific rules:\n{notes}\n\n"
        f"Hard avoids (never recommend these even as secondary options): {hard_avoid_str}\n\n"
        "You will receive JSON with pre-scored dining hall menus. The scores already "
        "reflect the preferences above. Use them as a strong signal but apply your own "
        "judgement too.\n\n"
        "Return a concise recommendation as JSON with exactly these keys:\n"
        "- best_hall: string\n"
        "- backup_hall: string\n"
        "- recommended_plate: list of strings (specific dishes to eat, max 6)\n"
        "- avoid: list of strings (specific dishes to skip, max 4)\n"
        '- confidence: "high", "medium", or "low"\n'
        "- reasoning: 2-3 sentence explanation\n\n"
        "Only return valid JSON. No markdown. No explanations outside the JSON."
    )


def _build_payload(
    menus: list[DiningHallMenu], scored: list[ScoredHall], date: str, meal: str
) -> str:
    by_id = {m.hall_id: m for m in menus}
    halls_summary = []
    for hall in scored:
        menu = by_id.get(hall.hall_id)
        halls_summary.append(
            {
                "hall": hall.hall_name,
                "score": hall.total_score,
                "positive_highlights": list(hall.breakdown.get("positive_hits", {}).keys()),
                "penalty_flags": list(hall.breakdown.get("penalty_hits", {}).keys()),
                "top_items": [it.name for it in hall.top_items[:6]],
                "avoid_items": [it.name for it in hall.avoid_items[:4]],
                "all_items": (
                    [{"name": it.name, "ingredients": it.ingredients} for it in menu.items]
                    if menu
                    else []
                ),
            }
        )
    return json.dumps({"date": date, "meal": meal, "halls": halls_summary}, indent=2)


def _generate_ai_recommendation(
    menus: list[DiningHallMenu],
    scored: list[ScoredHall],
    date: str,
    meal: str,
    *,
    prefs: dict[str, Any],
    api_key: str,
    model: str,
    base_url: str,
    timeout_s: float,
    transport: httpx.BaseTransport | None = None,
) -> DiningRecommendation:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _build_system_prompt(prefs)},
            {"role": "user", "content": _build_payload(menus, scored, date, meal)},
        ],
        "max_tokens": 800,
        "temperature": 0.2,
    }
    with httpx.Client(timeout=timeout_s, transport=transport) as client:
        resp = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

    data = json.loads(content)
    missing = _REQUIRED_KEYS - set(data)
    if missing:
        raise ValueError(f"LLM response missing keys: {sorted(missing)}")

    return DiningRecommendation(
        best_hall=str(data["best_hall"]),
        backup_hall=str(data["backup_hall"]),
        recommended_plate=[str(x) for x in data["recommended_plate"]],
        avoid=[str(x) for x in data["avoid"]],
        confidence=str(data["confidence"]),
        reasoning=str(data["reasoning"]),
        ai_generated=True,
        date=date,
        meal=meal,
    )


def get_recommendation(
    menus: list[DiningHallMenu],
    scored: list[ScoredHall],
    date: str,
    meal: str,
    *,
    prefs: dict[str, Any] | None = None,
    use_ai: bool = False,
    api_key: str = "",
    model: str = "gpt-4o-mini",
    base_url: str = "https://api.openai.com/v1",
    timeout_s: float = 20.0,
    transport: httpx.BaseTransport | None = None,
) -> DiningRecommendation:
    resolved_prefs = prefs or load_preferences()
    if use_ai and api_key:
        try:
            return _generate_ai_recommendation(
                menus,
                scored,
                date,
                meal,
                prefs=resolved_prefs,
                api_key=api_key,
                model=model,
                base_url=base_url,
                timeout_s=timeout_s,
                transport=transport,
            )
        except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "LLM recommendation failed (%s); using deterministic scoring", type(exc).__name__
            )
    return build_deterministic_recommendation(scored, date=date, meal=meal)
