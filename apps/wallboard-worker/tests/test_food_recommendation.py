from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest
from food_recommendation.provider import (
    FoodFixture,
    FoodRecommendationCache,
    MockFoodRecommendationProvider,
    Recommendation,
)
from wallboard_worker.jobs import run_food_recommendation
from wallboard_worker.settings import Settings


def test_mock_food_provider_is_stable_within_day_and_rotates() -> None:
    fixtures = (
        FoodFixture("A", "first"),
        FoodFixture("B", "second"),
        FoodFixture("C", "third"),
    )
    first_day = date(2026, 7, 7)
    next_day = date(2026, 7, 8)

    provider = MockFoodRecommendationProvider(today=lambda: first_day, fixtures=fixtures)
    first = provider.get_daily_recommendation()
    second = provider.get_daily_recommendation()

    assert first == second
    assert first.date == first_day
    assert first.provider == "mock"

    provider_next_day = MockFoodRecommendationProvider(today=lambda: next_day, fixtures=fixtures)
    assert provider_next_day.get_daily_recommendation().title != first.title


def test_food_cache_ignores_wrong_day(tmp_path) -> None:
    cache = FoodRecommendationCache(tmp_path / "food.json")
    cache.save(Recommendation(date=date(2026, 7, 7), title="Ramen", provider="mock"))

    assert cache.load_for_date(date(2026, 7, 7)) is not None
    assert cache.load_for_date(date(2026, 7, 8)) is None


@patch("httpx.post")
def test_run_food_recommendation_ingests_event(
    mock_post, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("FEATURE_FOOD", "True")
    monkeypatch.setenv("FOOD_RECOMMENDATION_CACHE_PATH", str(tmp_path / "food.json"))
    monkeypatch.setenv("CONTROL_PLANE_URL", "http://127.0.0.1:8090")
    monkeypatch.setenv("CONTROL_PLANE_ADMIN_TOKEN", "test-admin")

    settings = Settings()
    provider = MockFoodRecommendationProvider(today=lambda: date(2026, 7, 8))

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"token": "tok_ingest_123"}

    ingest_response = MagicMock()
    ingest_response.status_code = 200
    ingest_response.json.return_value = {"status": "stored"}

    mock_post.side_effect = [token_response, ingest_response]

    res = run_food_recommendation(
        settings,
        provider,
        now=datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC),
    )
    assert res is not None

    ingest_call = mock_post.mock_calls[1]
    body = ingest_call.kwargs["json"]

    batch_events = body["events"]
    assert len(batch_events) == 1
    event = batch_events[0]
    assert event["type"] == "ambient.food_recommendation"

    payload = event["payload"]
    assert payload["date"] == "2026-07-08"
    assert payload["title"]
    assert payload["detail"]
    assert payload["provider"] == "mock"


@patch("httpx.post")
def test_run_food_recommendation_falls_back_to_yesterday_cache(
    mock_post, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("FEATURE_FOOD", "True")
    monkeypatch.setenv("FOOD_RECOMMENDATION_CACHE_PATH", str(tmp_path / "food.json"))
    monkeypatch.setenv("CONTROL_PLANE_URL", "http://127.0.0.1:8090")
    monkeypatch.setenv("CONTROL_PLANE_ADMIN_TOKEN", "test-admin")

    settings = Settings()
    cache = FoodRecommendationCache(settings.food_cache_path)
    cache.save(
        Recommendation(
            date=date(2026, 7, 7),
            title="Yesterday's noodles",
            detail="Cached fallback",
            provider="mock",
        )
    )

    provider = MagicMock()
    provider.get_daily_recommendation.side_effect = RuntimeError("personal provider unavailable")

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"token": "tok_ingest_123"}

    ingest_response = MagicMock()
    ingest_response.status_code = 200
    ingest_response.json.return_value = {"status": "stored"}

    mock_post.side_effect = [token_response, ingest_response]

    res = run_food_recommendation(
        settings,
        provider,
        now=datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC),
        cache=cache,
    )
    assert res is not None

    ingest_call = mock_post.mock_calls[1]
    payload = ingest_call.kwargs["json"]["events"][0]["payload"]
    assert payload == {
        "date": "2026-07-07",
        "title": "Yesterday's noodles",
        "detail": "Cached fallback",
        "provider": "mock",
    }


@patch("httpx.post")
def test_run_food_recommendation_without_fallback_goes_stale_silently(
    mock_post, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("FEATURE_FOOD", "True")
    monkeypatch.setenv("FOOD_RECOMMENDATION_CACHE_PATH", str(tmp_path / "food.json"))

    settings = Settings()
    provider = MagicMock()
    provider.get_daily_recommendation.side_effect = RuntimeError("provider unavailable")

    res = run_food_recommendation(
        settings,
        provider,
        now=datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC),
    )

    assert res is None
    mock_post.assert_not_called()
