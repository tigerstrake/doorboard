"""Fixtures for door-media tests."""

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from door_media.app import app
from door_media.settings import Settings, override_settings, reset_settings
from fastapi.testclient import TestClient
from pydantic import Field


class TestSettings(Settings):
    media_mode: str = Field(default="mock")
    feature_photobooth: bool = Field(default=True)
    # Shorten retention loop defaults for testing if needed
    min_free_bytes: int = Field(default=1024 * 1024)
    storage_status_interval_s: int = Field(default=3600)  # Don't let loop spam logs
    admin_token: str = Field(default="test-admin-token")


@pytest.fixture(autouse=True)
def _setup_env(tmp_path: Path):
    """Ensure settings are overridden before any test runs."""
    ssd_root = tmp_path / "ssd"
    os.environ["SSD_DATA_ROOT"] = str(ssd_root)
    cfg = TestSettings()
    override_settings(cfg)
    yield
    reset_settings()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Return a TestClient. The app's lifespan will pick up the overridden settings."""
    with TestClient(app, headers={"Authorization": "Bearer test-admin-token"}) as c:
        yield c
