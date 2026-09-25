"""Enrollment key-release endpoint (ADR-0009 §6, Option C)."""

from __future__ import annotations

import logging

import pytest
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


# ---------------------------------------------------------------------------
# Hardening: failed attempts are logged and rate limited, the release names its peer
# ---------------------------------------------------------------------------

_AUTH_LOGGER = "control_plane_api.auth"


def test_six_bad_tokens_in_a_minute_are_refused_and_logged_without_the_secret(
    engine, caplog: pytest.LogCaptureFixture
) -> None:  # noqa: ANN001, ARG001
    """The route hands out the LUKS passphrase for the biometric volume (ADR-0009 §6).

    Before: unlimited silent guesses on a LAN-bound port.
    """
    override_settings(_KeySettings())
    with TestClient(app) as c, caplog.at_level(logging.WARNING, logger=_AUTH_LOGGER):
        codes = [
            c.get(
                "/status/keys/enrollment", headers={"Authorization": f"Bearer wrong-{i}"}
            ).status_code
            for i in range(6)
        ]
        assert codes == [401, 401, 401, 401, 401, 429]
        # Locked out for the window even with the RIGHT token.
        blocked = c.get(
            "/status/keys/enrollment", headers={"Authorization": "Bearer release-token-xyz"}
        )
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "60"
        assert "key" not in blocked.json()

    failures = [r for r in caplog.records if r.getMessage() == "auth_failed"]
    assert len(failures) == 5
    for record in failures:
        assert record.route == "/status/keys/enrollment"  # type: ignore[attr-defined]
        assert record.peer_ip == "testclient"  # type: ignore[attr-defined]
        assert record.scheme == "enrollment_key"  # type: ignore[attr-defined]
        assert record.reason == "invalid_token"  # type: ignore[attr-defined]
    limited = [r for r in caplog.records if r.getMessage() == "auth_rate_limited"]
    assert len(limited) == 2
    # Never the presented value, never the real token, never the key.
    everything = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for secret in ("wrong-0", "release-token-xyz", "luks-pass-abc"):
        assert secret not in everything


def test_a_missing_header_is_logged_but_does_not_count_toward_the_lockout(
    engine, caplog: pytest.LogCaptureFixture
) -> None:  # noqa: ANN001, ARG001
    override_settings(_KeySettings())
    with TestClient(app) as c, caplog.at_level(logging.WARNING, logger=_AUTH_LOGGER):
        for _ in range(10):
            assert c.get("/status/keys/enrollment").status_code == 401
        ok = c.get("/status/keys/enrollment", headers={"Authorization": "Bearer release-token-xyz"})
        assert ok.status_code == 200
    reasons = {r.reason for r in caplog.records if r.getMessage() == "auth_failed"}  # type: ignore[attr-defined]
    assert reasons == {"missing_token"}


def test_the_release_log_line_names_the_peer(engine, caplog: pytest.LogCaptureFixture) -> None:  # noqa: ANN001, ARG001
    override_settings(_KeySettings())
    with (
        TestClient(app) as c,
        caplog.at_level(logging.INFO, logger="control_plane_api.keys"),
    ):
        c.get("/status/keys/enrollment", headers={"Authorization": "Bearer release-token-xyz"})
    released = [r for r in caplog.records if r.getMessage() == "enrollment_key_released"]
    assert len(released) == 1
    assert released[0].peer_ip == "testclient"  # type: ignore[attr-defined]
    assert "luks-pass-abc" not in f"{released[0].__dict__}"
