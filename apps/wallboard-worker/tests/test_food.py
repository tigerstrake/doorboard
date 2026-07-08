from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from food_recommendation.provider import (
    FoodRecommendationProvider,
    MockFoodRecommendationProvider,
    Recommendation,
)
from wallboard_worker.jobs import run_food_recommendation
from wallboard_worker.settings import Settings


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOORBOARD_DOOR_ID", "primary")
    monkeypatch.setenv("CONTROL_PLANE_URL", "http://mock-control-plane")
    monkeypatch.setenv("CONTROL_PLANE_ADMIN_TOKEN", "secret")


class FailingFoodProvider(FoodRecommendationProvider):
    def __init__(self, fail_all: bool = False):
        self.fail_all = fail_all
        self.calls = []

    def get_daily_recommendation(self, dt: date) -> Recommendation:
        self.calls.append(dt)
        if self.fail_all:
            raise RuntimeError("Outage!")
        if dt == date(2026, 7, 8):
            raise RuntimeError("Today is broken!")
        return Recommendation(
            date=dt,
            title="Yesterday's Tacos",
            detail="Leftovers",
            provider="test-failing",
        )


def test_food_recommendation_job_success(monkeypatch: Any) -> None:
    settings = Settings(
        door_id="primary",
        control_plane_url="http://mock-control-plane",
        control_plane_admin_token="secret",
    )
    provider = MockFoodRecommendationProvider()

    token_called = False
    ingest_called = False

    def mock_post(url: str, **kwargs: Any) -> httpx.Response:
        nonlocal token_called, ingest_called
        if "/admin/tokens" in url:
            token_called = True
            assert kwargs["json"] == {"scope": "ingest", "door_id": "primary"}
            assert kwargs["headers"]["Authorization"] == "Bearer secret"
            return httpx.Response(200, json={"token": "mock-ingest-token"})
        elif "/ingest" in url:
            ingest_called = True
            assert kwargs["headers"]["Authorization"] == "Bearer mock-ingest-token"
            batch = kwargs["json"]
            assert batch["batch_id"].startswith("worker-food-")
            assert len(batch["events"]) == 1
            event = batch["events"][0]
            assert event["type"] == "ambient.food_recommendation"
            assert event["payload"]["title"] == "Chicken Tikka Masala"
            return httpx.Response(
                200, json={"results": [{"event_id": event["event_id"], "status": "stored"}]}
            )
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "post", mock_post)

    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)
    res = run_food_recommendation(settings, provider, now=now)
    assert res is not None
    assert token_called
    assert ingest_called


def test_food_recommendation_job_fallback_yesterday(monkeypatch: Any) -> None:
    settings = Settings(
        door_id="primary",
        control_plane_url="http://mock-control-plane",
    )
    # Target date date(2026, 7, 8) fails, but date(2026, 7, 7) succeeds
    provider = FailingFoodProvider(fail_all=False)

    token_called = False
    ingest_called = False

    def mock_post(url: str, **kwargs: Any) -> httpx.Response:
        nonlocal token_called, ingest_called
        if "/admin/tokens" in url:
            token_called = True
            return httpx.Response(200, json={"token": "mock-ingest-token"})
        elif "/ingest" in url:
            ingest_called = True
            assert kwargs["headers"]["Authorization"] == "Bearer mock-ingest-token"
            batch = kwargs["json"]
            event = batch["events"][0]
            assert event["payload"]["title"] == "Yesterday's Tacos"
            assert event["payload"]["date"] == "2026-07-07"
            return httpx.Response(200, json={"status": "stored"})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "post", mock_post)

    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)
    res = run_food_recommendation(settings, provider, now=now)
    assert res is not None
    assert token_called
    assert ingest_called
    assert provider.calls == [date(2026, 7, 8), date(2026, 7, 7)]


def test_food_recommendation_job_total_failure(monkeypatch: Any) -> None:
    settings = Settings(
        door_id="primary",
        control_plane_url="http://mock-control-plane",
    )
    provider = FailingFoodProvider(fail_all=True)

    def mock_post(url: str, **kwargs: Any) -> httpx.Response:
        if "/admin/tokens" in url:
            return httpx.Response(200, json={"token": "mock-ingest-token"})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "post", mock_post)

    now = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)
    res = run_food_recommendation(settings, provider, now=now)
    assert res is None
    assert provider.calls == [date(2026, 7, 8), date(2026, 7, 7)]
