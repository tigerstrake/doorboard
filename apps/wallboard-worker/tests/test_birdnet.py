from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from birdnet.provider import BirdnetConfig, BirdnetGoProvider, MockBirdProvider
from wallboard_worker.jobs import run_bird_summary
from wallboard_worker.settings import Settings


def test_mock_bird_provider() -> None:
    provider = MockBirdProvider()
    total, top = provider.get_summary(datetime.now(UTC))
    assert total == 7
    assert len(top) == 2
    assert top[0]["name"] == "House Finch"
    assert top[0]["count"] == 4
    assert top[0]["confidence_avg"] == 0.88


def test_birdnet_go_provider_success() -> None:
    config = BirdnetConfig(
        url="http://127.0.0.1:8080",
        confidence_threshold=0.70,
        species_filter=["House Finch", "Mourning Dove"],
    )
    provider = BirdnetGoProvider(config)
    now = datetime(2026, 7, 7, 12, 0, 0, tzinfo=UTC)

    # API response simulation
    mock_detections = [
        # Match (House Finch, confidence > 0.70, today)
        {
            "commonName": "House Finch",
            "scientificName": "Haemorhous mexicanus",
            "confidence": 0.85,
            "date": "2026-07-07 10:00:00",
        },
        # Match (Mourning Dove, confidence > 0.70, today)
        {
            "commonName": "Mourning Dove",
            "scientificName": "Zenaida macroura",
            "confidence": 0.78,
            "date": "2026-07-07 11:30:00",
        },
        # Filtered out (too low confidence)
        {
            "commonName": "House Finch",
            "scientificName": "Haemorhous mexicanus",
            "confidence": 0.65,
            "date": "2026-07-07 11:45:00",
        },
        # Filtered out (not today)
        {
            "commonName": "House Finch",
            "scientificName": "Haemorhous mexicanus",
            "confidence": 0.90,
            "date": "2026-07-06 12:00:00",
        },
        # Filtered out (not in species list)
        {
            "commonName": "American Crow",
            "scientificName": "Corvus brachyrhynchos",
            "confidence": 0.95,
            "date": "2026-07-07 09:00:00",
        },
    ]

    with patch("httpx.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_detections
        mock_get.return_value = mock_response

        total, top = provider.get_summary(now)

        # Assert correct filtering
        assert total == 2
        assert len(top) == 2
        assert top[0]["name"] == "House Finch"
        assert top[0]["count"] == 1
        assert top[0]["confidence_avg"] == 0.85

        assert top[1]["name"] == "Mourning Dove"
        assert top[1]["count"] == 1
        assert top[1]["confidence_avg"] == 0.78


def test_birdnet_go_provider_api_error_graceful() -> None:
    config = BirdnetConfig(url="http://127.0.0.1:8080")
    provider = BirdnetGoProvider(config)

    with patch("httpx.get") as mock_get:
        mock_get.side_effect = Exception("Connection refused")

        with pytest.raises(RuntimeError, match="Unreachable"):
            provider.get_summary(datetime.now(UTC))


def test_run_bird_summary_job_failure_degrades_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_BIRDNET", "True")
    monkeypatch.setenv("BIRDNET_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("WALLBOARD_WORKER_INGEST_TOKEN", "test-ingest-token")
    settings = Settings()
    mock_provider = MagicMock()
    mock_provider.get_summary.side_effect = Exception("Bird Pi Unreachable")

    # The job must not crash, but return None to indicate failure (stale path)
    res = run_bird_summary(settings, mock_provider)
    assert res is None


@patch("httpx.post")
def test_run_bird_summary_job_success(mock_post, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_BIRDNET", "True")
    monkeypatch.setenv("BIRDNET_URL", "mock")
    monkeypatch.setenv("CONTROL_PLANE_URL", "http://127.0.0.1:8090")
    monkeypatch.setenv("CONTROL_PLANE_ADMIN_TOKEN", "test-admin")
    settings = Settings()
    provider = MockBirdProvider()

    # Mock admin tokens endpoint and ingest endpoint
    # First post is admin/tokens, second is ingest
    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {"token": "tok_ingest_123"}

    ingest_response = MagicMock()
    ingest_response.status_code = 200
    ingest_response.json.return_value = {"status": "stored"}

    mock_post.side_effect = [token_response, ingest_response]

    res = run_bird_summary(settings, provider)
    assert res is not None

    # Verify calls
    assert mock_post.call_count == 2
    token_call, ingest_call = mock_post.call_args_list

    # Admin tokens call checks
    assert "admin/tokens" in token_call[0][0]
    assert token_call[1]["json"] == {"scope": "ingest", "door_id": "primary"}

    # Ingest call checks
    assert "ingest" in ingest_call[0][0]
    assert ingest_call[1]["headers"]["Authorization"] == "Bearer tok_ingest_123"
    batch = ingest_call[1]["json"]
    assert len(batch["events"]) == 1
    event = batch["events"][0]
    assert event["type"] == "ambient.bird_summary"
    assert event["payload"]["total_detections"] == 7
