"""Offline tests for the real Stanford dining food provider (T-607).

Everything runs without network: HTTP is faked with ``httpx.MockTransport`` and
the ASP.NET page is a saved fixture. Covers HTML parsing (html.parser backend),
preference scoring, the deterministic recommendation, the LLM path + fallback,
and the end-to-end provider mapping onto the public ``Recommendation``.
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from food_recommendation.provider import MockFoodRecommendationProvider
from food_recommendation.stanford.models import DiningHallMenu, MenuItem
from food_recommendation.stanford.provider import (
    StanfordDiningConfig,
    StanfordDiningProvider,
)
from food_recommendation.stanford.scorer import (
    build_deterministic_recommendation,
    score_all_halls,
    score_item,
)
from food_recommendation.stanford.scraper import (
    _extract_day_options,
    _extract_meal_options,
    _soup,
    parse_menu_html,
)

FIXTURE = Path(__file__).parent / "fixtures" / "wilbur_lunch_2026_05_11.html"


@pytest.fixture
def wilbur_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _make_item(name: str, ingredients: str = "") -> MenuItem:
    return MenuItem(name=name, ingredients=ingredients)


# ---------------- scraper (html.parser backend) ----------------


def test_parse_fixture_finds_items_and_names(wilbur_html: str) -> None:
    menu = parse_menu_html(wilbur_html, "Wilbur", "Wilbur Dining", "5/11/2026", "Lunch")
    names = [it.name for it in menu.items]
    assert len(menu.items) > 0
    assert "Grilled Chicken" in names
    assert "Black Eyed Peas" in names
    assert "Blackened Salmon" in names


def test_parse_fixture_ingredients_and_flags(wilbur_html: str) -> None:
    menu = parse_menu_html(wilbur_html, "Wilbur", "Wilbur Dining", "5/11/2026", "Lunch")
    salmon = next(it for it in menu.items if it.name == "Blackened Salmon")
    assert "salmon" in salmon.ingredients.lower()
    assert "FISH" in salmon.allergens.upper()
    tofu = next(it for it in menu.items if "Tofu" in it.name)
    assert tofu.is_vegan and tofu.is_gluten_free


def test_extract_day_and_meal_options(wilbur_html: str) -> None:
    soup = _soup(wilbur_html)
    assert "5/11/2026" in _extract_day_options(soup)
    meals = _extract_meal_options(soup)
    assert {"Breakfast", "Lunch", "Dinner", "Brunch"} <= set(meals)


def test_parse_empty_html_does_not_crash() -> None:
    menu = parse_menu_html("<html><body></body></html>", "T", "Test", "5/11/2026", "Lunch")
    assert menu.items == []


# ---------------- scorer ----------------


def test_scoring_directions() -> None:
    assert score_item(_make_item("Grilled Chicken", "chicken breast, olive oil"))[0] > 0
    assert score_item(_make_item("Blackened Salmon", "salmon, cajun spice"))[0] < 0
    assert score_item(_make_item("Black Eyed Peas", "black eyed peas, garlic"))[0] > 0
    assert score_item(_make_item("Chocolate Brownie", "flour, sugar, cocoa"))[0] < 0


def test_score_all_halls_sorts_best_first_and_recommends() -> None:
    good = DiningHallMenu(
        hall_name="Good",
        hall_id="good",
        date="2026-05-11",
        meal="Lunch",
        items=[
            _make_item("Grilled Chicken", "chicken breast"),
            _make_item("Lentil Soup", "lentils"),
        ],
    )
    bad = DiningHallMenu(
        hall_name="Bad",
        hall_id="bad",
        date="2026-05-11",
        meal="Lunch",
        items=[
            _make_item("Fried Shrimp", "shrimp, deep fried"),
            _make_item("Cake", "sugar, flour"),
        ],
    )
    scored = score_all_halls([bad, good])
    assert scored[0].hall_name == "Good"
    rec = build_deterministic_recommendation(scored, "2026-05-11", "Lunch")
    assert rec.best_hall == "Good"
    assert rec.recommended_plate
    assert not rec.ai_generated


def test_deterministic_recommendation_with_no_items() -> None:
    scored = score_all_halls(
        [DiningHallMenu(hall_name="E", hall_id="e", date="2026-05-11", meal="Lunch", items=[])]
    )
    rec = build_deterministic_recommendation(scored, "2026-05-11", "Lunch")
    assert rec.best_hall == "None"
    assert rec.confidence == "low"


# ---------------- provider (end-to-end, mocked HTTP) ----------------

# Monday 2026-05-11 12:00 America/Los_Angeles (PDT) -> Lunch, date 5/11/2026.
FIXED_NOW = datetime(2026, 5, 11, 19, 0, tzinfo=UTC)


def _menu_transport(html: str) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    return httpx.MockTransport(handler)


def _openai_transport(content: dict[str, object]) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(content)}}]},
        )

    return httpx.MockTransport(handler)


def test_provider_maps_to_public_recommendation(wilbur_html: str) -> None:
    provider = StanfordDiningProvider(
        StanfordDiningConfig(request_delay_s=0.0, hall_ids=["Wilbur"]),
        now=lambda: FIXED_NOW,
        http_transport=_menu_transport(wilbur_html),
    )
    rec = provider.get_daily_recommendation()
    assert rec.date == date(2026, 5, 11)
    assert rec.provider == "stanford"
    assert " — Lunch" in rec.title
    assert rec.detail and "confidence" in rec.detail


def test_provider_raises_when_no_menu_items() -> None:
    empty_page = (
        "<html><body><select id='MainContent_lstLocations'>"
        "<option value='Wilbur'>Wilbur Dining</option></select></body></html>"
    )
    provider = StanfordDiningProvider(
        StanfordDiningConfig(request_delay_s=0.0, hall_ids=["Wilbur"]),
        now=lambda: FIXED_NOW,
        http_transport=_menu_transport(empty_page),
    )
    with pytest.raises(RuntimeError):
        provider.get_daily_recommendation()


def test_provider_uses_ai_when_configured(wilbur_html: str) -> None:
    ai_pick = {
        "best_hall": "Wilbur Dining",
        "backup_hall": "Wilbur Dining",
        "recommended_plate": ["Grilled Chicken", "Roasted Vegetables"],
        "avoid": ["Blackened Salmon"],
        "confidence": "high",
        "reasoning": "Strong whole-food protein and vegetables.",
    }
    provider = StanfordDiningProvider(
        StanfordDiningConfig(
            request_delay_s=0.0, hall_ids=["Wilbur"], use_ai=True, openai_api_key="sk-test"
        ),
        now=lambda: FIXED_NOW,
        http_transport=_menu_transport(wilbur_html),
        openai_transport=_openai_transport(ai_pick),
    )
    rec = provider.get_daily_recommendation()
    assert rec.provider == "stanford-ai"
    assert "Grilled Chicken" in (rec.detail or "")


def test_provider_falls_back_when_ai_fails(wilbur_html: str) -> None:
    def failing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    provider = StanfordDiningProvider(
        StanfordDiningConfig(
            request_delay_s=0.0, hall_ids=["Wilbur"], use_ai=True, openai_api_key="sk-test"
        ),
        now=lambda: FIXED_NOW,
        http_transport=_menu_transport(wilbur_html),
        openai_transport=httpx.MockTransport(failing),
    )
    rec = provider.get_daily_recommendation()
    # AI failed -> deterministic path -> provider tag stays "stanford".
    assert rec.provider == "stanford"


# ---------------- settings + CLI provider selection ----------------


def test_settings_food_hall_id_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from wallboard_worker.settings import Settings

    monkeypatch.setenv("FOOD_HALL_IDS", " Wilbur , Stern ,")
    assert Settings().food_hall_id_list() == ["Wilbur", "Stern"]
    monkeypatch.setenv("FOOD_HALL_IDS", "")
    assert Settings().food_hall_id_list() == []


def _run_food_cli(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> object:
    # __init__ re-exports the `cli` group, so import the module explicitly.
    cli_module = importlib.import_module("wallboard_worker.cli")

    for key in ("FEATURE_FOOD", "FOOD_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    captured: dict[str, object] = {}

    def _capture(settings: object, provider: object, *args: object, **kwargs: object) -> None:
        captured["provider"] = provider

    monkeypatch.setattr(cli_module, "run_food_recommendation", _capture)
    result = CliRunner().invoke(cli_module.cli, ["food-recommendation"])
    assert result.exit_code == 0, result.output
    return captured["provider"]


def test_cli_defaults_to_mock_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _run_food_cli(monkeypatch, {})
    assert isinstance(provider, MockFoodRecommendationProvider)


def test_cli_selects_stanford_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _run_food_cli(monkeypatch, {"FEATURE_FOOD": "true", "FOOD_PROVIDER": "stanford"})
    assert isinstance(provider, StanfordDiningProvider)


def test_cli_stanford_flag_off_still_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    # FOOD_PROVIDER=stanford but FEATURE_FOOD unset -> mock (feature gate wins).
    provider = _run_food_cli(monkeypatch, {"FOOD_PROVIDER": "stanford"})
    assert isinstance(provider, MockFoodRecommendationProvider)
