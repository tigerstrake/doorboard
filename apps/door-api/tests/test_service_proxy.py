"""The admin surface reaching loopback-only services through door-api (ADR-0024).

door-visiond and door-media bind 127.0.0.1 on purpose. The admin page called them directly
from the browser, so from anywhere but the Pi every enrollment, invite, arrival-log and
privacy panel failed — and rendered the failure as a fact: "Enrolled Members (0)" for a door
with two enrolled, "Relay not configured" for a configured relay.

These pin both halves: the owner's browser can reach what it needs, and door-api's admin
token does not become a skeleton key for everything those services expose.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state  # noqa: E402
from door_api.service_proxy import (  # noqa: E402
    MEDIA_ROUTES,
    VISIOND_ROUTES,
    ProxyDenied,
    resolve,
)

TOKEN = "social-admin-token-for-tests"
VISIOND_TOKEN = "visiond-token-the-browser-must-never-see"


@pytest.fixture(autouse=True)
def _app_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("DOOR_VISIOND_ADMIN_TOKEN", VISIOND_TOKEN)
    state.__init__()
    state.startup()
    yield
    state.shutdown()


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


class _Recorder:
    """Stands in for the loopback service, recording what door-api sent it."""

    def __init__(self, *, status: int = 200, body: bytes = b'{"ok":true}') -> None:
        self.requests: list[httpx.Request] = []
        self._status = status
        self._body = body

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            self._status, content=self._body, headers={"content-type": "application/json"}
        )


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    transport = httpx.MockTransport(recorder.handler)
    original = httpx.AsyncClient.__init__

    def _patched(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = transport
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched)
    return recorder


class TestAllowList:
    """The list is the security boundary, so it is tested as one."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/people"),
            ("GET", "/consent"),
            ("GET", "/health"),
            ("GET", "/relay-status"),
            ("GET", "/invites"),
            ("GET", "/visits"),
            ("GET", "/visits/counts"),
            ("GET", "/current-visitor"),
            ("POST", "/invites"),
            ("POST", "/invites/inv_abc123/revoke"),
            ("POST", "/unenroll"),
            ("POST", "/visits/purge"),
            ("POST", "/privacy-mode"),
        ],
    )
    def test_the_admin_page_can_reach_what_it_needs(self, method: str, path: str) -> None:
        assert resolve(VISIOND_ROUTES, method, path)

    @pytest.mark.parametrize(
        ("method", "path", "why"),
        [
            ("POST", "/enroll", "raw face images must not cross a LAN-exposed route"),
            ("GET", "/metrics", "not something the admin page reads"),
            ("POST", "/relay-key/rotate", "not an admin-page action"),
            ("GET", "/self-enroll/invites", "the doorpad's own unauthenticated route"),
            ("DELETE", "/people", "no delete verb is proxied at all"),
        ],
    )
    def test_everything_else_is_refused(self, method: str, path: str, why: str) -> None:
        """door-api's admin token must not be a skeleton key for door-visiond."""
        with pytest.raises(ProxyDenied):
            resolve(VISIOND_ROUTES, method, path)

    def test_the_method_matters(self) -> None:
        """A GET-only route must not be reachable by POST, or vice versa."""
        assert resolve(VISIOND_ROUTES, "GET", "/people")
        with pytest.raises(ProxyDenied):
            resolve(VISIOND_ROUTES, "POST", "/people")

    def test_patterns_are_anchored(self) -> None:
        """Unanchored patterns are how allow-lists leak."""
        for path in (
            "/people/../enroll",
            "/peoplex",
            "/x/people",
            "/invites/a/revoke/../../enroll",
        ):
            with pytest.raises(ProxyDenied):
                resolve(VISIOND_ROUTES, "GET", path)

    def test_media_exposes_only_the_still(self) -> None:
        assert resolve(MEDIA_ROUTES, "GET", "/snapshot")
        for path in ("/recordings", "/health", "/metrics", "/internal/session_event"):
            with pytest.raises(ProxyDenied):
                resolve(MEDIA_ROUTES, "GET", path)


class TestProxyBehaviour:
    def test_it_forwards_and_returns_the_service_response(self, service: _Recorder) -> None:
        response = TestClient(app).get("/admin/visiond/people", headers=_auth())

        assert response.status_code == 200
        assert len(service.requests) == 1
        assert service.requests[0].url.path == "/people"

    def test_the_browser_never_sees_the_service_token(self, service: _Recorder) -> None:
        """The point of proxying: the capability moves, the credential does not."""
        response = TestClient(app).get("/admin/visiond/people", headers=_auth())

        # door-api presented door-visiond's token upstream...
        assert service.requests[0].headers["authorization"] == f"Bearer {VISIOND_TOKEN}"
        # ...and nothing about it came back down to the caller.
        assert VISIOND_TOKEN not in response.text
        assert VISIOND_TOKEN not in str(response.headers)

    def test_the_query_string_is_forwarded(self, service: _Recorder) -> None:
        TestClient(app).get("/admin/visiond/visits?limit=5&person_id=prs_1", headers=_auth())

        forwarded = service.requests[0].url
        assert forwarded.params["limit"] == "5"
        assert forwarded.params["person_id"] == "prs_1"

    def test_a_post_body_is_forwarded(self, service: _Recorder) -> None:
        TestClient(app).post(
            "/admin/visiond/unenroll", json={"person_id": "prs_1"}, headers=_auth()
        )

        assert b"prs_1" in service.requests[0].content

    def test_a_denied_path_is_403_and_never_forwarded(self, service: _Recorder) -> None:
        response = TestClient(app).post("/admin/visiond/enroll", headers=_auth())

        assert response.status_code == 403
        assert service.requests == [], "a refused route must not reach the service at all"

    def test_it_requires_the_admin_token(self, service: _Recorder) -> None:
        response = TestClient(app).get("/admin/visiond/people")

        assert response.status_code in (401, 403)
        assert service.requests == []

    def test_an_unreachable_service_is_a_named_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """So the page can say which service is down instead of drawing an empty list."""

        def _explode(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport = httpx.MockTransport(_explode)
        original = httpx.AsyncClient.__init__

        def _patched(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            original(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched)

        response = TestClient(app).get("/admin/visiond/people", headers=_auth())

        assert response.status_code == 503
        assert "door-visiond" in response.json()["detail"]
