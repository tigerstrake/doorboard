"""T-312 / ADR-0019 — door-api forwarding the doorpad's self-enrollment request.

door-api is a forwarder here and nothing more: every cap lives in door-visiond, which
owns the enrollment store. What these pin is that it stays a forwarder — no credential
of its own, and refusals arriving with their reason intact, because the doorpad has to
say *why* enrollment is closed rather than showing a shrug.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

# Set before importing door_api.app: the module builds its state at import time and
# refuses to start without a database location (same pattern as test_app.py).
os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class _FakeVisiondResponse:
    def __init__(self, status_code: int, body: Any, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self) -> Any:
        return self._body


class _FakeVisiondClient:
    """Stands in for httpx.AsyncClient, recording what door-api sent to visiond."""

    response: _FakeVisiondResponse = _FakeVisiondResponse(201, {})
    calls: list[tuple[str, dict[str, str]]] = []
    raise_on_post: bool = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def __aenter__(self) -> _FakeVisiondClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args

    async def post(
        self, url: str, *, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> _FakeVisiondResponse:
        del kwargs
        if type(self).raise_on_post:
            msg = "connection refused"
            raise OSError(msg)
        type(self).calls.append((url, headers or {}))
        return type(self).response


@pytest.fixture(autouse=True)
def _stub_visiond(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeVisiondClient.calls = []
    _FakeVisiondClient.raise_on_post = False
    _FakeVisiondClient.response = _FakeVisiondResponse(
        201,
        {
            "invite_id": "inv_abc",
            "url": "https://relay.example.test/e/inv_abc.secret#k=fp",
            "expires_at": "2026-08-16T21:00:00+00:00",
        },
    )
    monkeypatch.setattr("door_api.app.httpx.AsyncClient", _FakeVisiondClient)


def test_doorpad_mints_an_invite_without_any_credential() -> None:
    response = TestClient(app).post("/doorpad/enroll-invite")

    assert response.status_code == 201
    assert response.json()["url"].endswith("#k=fp")
    url, headers = _FakeVisiondClient.calls[0]
    assert url == "http://127.0.0.1:8081/self-enroll/invites"
    # ADR-0019: the kiosk holds no token, and door-api does not invent one either --
    # the visiond route is loopback-only and unauthenticated by design.
    assert "Authorization" not in headers


def test_no_body_is_forwarded_so_a_visitor_cannot_forge_a_label() -> None:
    """The hourly cap is counted by label; a caller-set label would escape it."""
    response = TestClient(app).post("/doorpad/enroll-invite", json={"label": "owner-looking"})

    assert response.status_code == 201
    assert _FakeVisiondClient.calls, "request never reached visiond"


@pytest.mark.parametrize(
    ("upstream_status", "reason"),
    [
        (429, "rate_limited"),
        (409, "door_full"),
    ],
)
def test_refusals_keep_their_status_and_reason(upstream_status: int, reason: str) -> None:
    _FakeVisiondClient.response = _FakeVisiondResponse(
        upstream_status,
        {"detail": {"error": "self_enroll_closed", "reason": reason}},
        headers={"Retry-After": "3600"} if upstream_status == 429 else None,
    )

    response = TestClient(app).post("/doorpad/enroll-invite")

    assert response.status_code == upstream_status
    assert response.json()["detail"]["reason"] == reason
    if upstream_status == 429:
        assert response.headers["Retry-After"] == "3600"


def test_locked_enrollment_volume_is_reported_as_503() -> None:
    _FakeVisiondClient.response = _FakeVisiondResponse(
        503, {"detail": "encrypted enrollment storage is locked"}
    )

    response = TestClient(app).post("/doorpad/enroll-invite")

    assert response.status_code == 503
    assert "locked" in str(response.json()["detail"])


def test_an_unreachable_visiond_is_503_not_a_traceback() -> None:
    _FakeVisiondClient.raise_on_post = True

    response = TestClient(app).post("/doorpad/enroll-invite")

    assert response.status_code == 503
    assert response.json()["detail"] == "door-visiond unavailable"


def test_an_unexpected_upstream_status_does_not_leak_through() -> None:
    """A 500 from visiond must not surface as a 500-with-internals on a public route."""
    _FakeVisiondClient.response = _FakeVisiondResponse(500, {"detail": "stack trace innards"})

    response = TestClient(app).post("/doorpad/enroll-invite")

    assert response.status_code == 503
    assert "innards" not in response.text
