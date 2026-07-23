"""Enrollment key-release endpoint (ADR-0009 §6, Option C)."""

from __future__ import annotations

from control_plane_api.app import app
from control_plane_api.settings import override_settings
from fastapi.testclient import TestClient

from .conftest import TestSettings


class _KeySettings(TestSettings):
    # Class-level field overrides (the alias-safe way — mirrors conftest's
    # TestSettings; passing these as init kwargs hits the field aliases).
    enrollment_key: str = "luks-pass-abc"
    enrollment_key_token: str = "release-token-xyz"


def test_release_returns_key_with_correct_token(engine) -> None:  # noqa: ANN001, ARG001
    override_settings(_KeySettings())
    with TestClient(app) as c:
        resp = c.get(
            "/status/keys/enrollment",
            headers={"Authorization": "Bearer release-token-xyz"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"key": "luks-pass-abc"}


def test_release_rejects_wrong_and_missing_token(engine) -> None:  # noqa: ANN001, ARG001
    override_settings(_KeySettings())
    with TestClient(app) as c:
        assert (
            c.get("/status/keys/enrollment", headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        assert c.get("/status/keys/enrollment").status_code == 401


def test_release_absent_when_unconfigured(client: TestClient) -> None:
    # Default TestSettings sets neither key nor token -> feature off (404).
    resp = client.get("/status/keys/enrollment", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 404
