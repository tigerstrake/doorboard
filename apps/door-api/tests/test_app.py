"""Smoke test for the door-api ASGI app."""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from typing import Any

import pytest
from doorboard_contracts import SessionState
from doorboard_simulator.clock import SimClock
from doorboard_simulator.events import EventFactory
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state


@pytest.fixture(autouse=True)
def _mock_env_for_test(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    # Force the app to use an in-memory DB for tests, avoiding the RuntimeError
    # from config.py if the environment isn't fully set up.
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    # Re-initialize state to pick up the test env
    state.__init__()
    state.startup()
    yield
    state.shutdown()


def test_health() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"service": "door-api", "status": "ok", "detail": None}


def test_metrics() -> None:
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "session_transitions_total" in response.text
    assert "door_api_media_outbox_depth" in response.text


def test_admin_routes_fail_closed_without_configured_token() -> None:
    client = TestClient(app)

    for method, path in (
        ("get", "/admin/media-inbox"),
        ("get", "/admin/media-inbox/recording-1/file"),
        ("get", "/admin/recordings"),
        ("delete", "/admin/recordings/recording-1"),
        ("post", "/admin/session/answer"),
        ("post", "/admin/session/cannot-answer"),
        ("post", "/admin/session/end"),
        ("get", "/admin/visitor-collage"),
    ):
        response = client.request(method, path)
        assert response.status_code == 503


class _FakeMediaResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        body: Any = None,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body
        self.content = content
        self.headers = headers or {}

    def json(self) -> Any:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"upstream status {self.status_code}")


class _FakeMediaClient:
    file_status = 200
    calls: list[tuple[str, dict[str, str]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def __aenter__(self) -> _FakeMediaClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args

    async def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeMediaResponse:
        del params
        request_headers = headers or {}
        self.calls.append((url, request_headers))
        if url.endswith("/recordings"):
            return _FakeMediaResponse(
                body={
                    "recordings": [
                        {
                            "recording_id": "recording-1",
                            "session_id": "session-1",
                            "kind": "video_message",
                        }
                    ]
                }
            )
        return _FakeMediaResponse(
            status_code=self.file_status,
            content=b"local-video",
            headers={"content-type": "text/html"},
        )


def _enable_admin(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("DOOR_API_SOCIAL_ADMIN_TOKEN", "owner-token")
    monkeypatch.setenv("DOOR_MEDIA_ADMIN_TOKEN", "media-token")
    state.shutdown()
    state.__init__()
    state.startup()
    return {"Authorization": "Bearer owner-token"}


def test_admin_video_playback_is_authenticated_no_store_and_forces_video_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = _enable_admin(monkeypatch)
    _FakeMediaClient.file_status = 200
    _FakeMediaClient.calls = []
    monkeypatch.setattr("door_api.app.httpx.AsyncClient", _FakeMediaClient)

    response = TestClient(app).get("/admin/media-inbox/recording-1/file", headers=headers)

    assert response.status_code == 200
    assert response.content == b"local-video"
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert all(
        call_headers == {"Authorization": "Bearer media-token"}
        for _, call_headers in _FakeMediaClient.calls
    )


@pytest.mark.parametrize(("upstream_status", "expected_status"), [(404, 404), (500, 503)])
def test_admin_video_playback_reports_upstream_failure_without_leaking_body(
    monkeypatch: pytest.MonkeyPatch,
    upstream_status: int,
    expected_status: int,
) -> None:
    headers = _enable_admin(monkeypatch)
    _FakeMediaClient.file_status = upstream_status
    _FakeMediaClient.calls = []
    monkeypatch.setattr("door_api.app.httpx.AsyncClient", _FakeMediaClient)

    response = TestClient(app).get("/admin/media-inbox/recording-1/file", headers=headers)

    assert response.status_code == expected_status
    assert "local-video" not in response.text


def test_websocket_broadcast_smoke() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        # First message should be the snapshot.
        data = websocket.receive_text()
        snapshot = json.loads(data)
        assert snapshot["type"] == "snapshot"
        assert snapshot["state"]["state"] == "IDLE"

        # Trigger a transition on the machine directly.
        state.machine.handle_button_pressed()

        # The machine will emit multiple events (BUTTON_PRESSED then VISITOR_MODE).
        # And we get deltas for them.

        # Read the state_changed for BUTTON_PRESSED
        delta1_raw = websocket.receive_text()
        delta1 = json.loads(delta1_raw)
        assert delta1["type"] == "delta"
        assert delta1["event"]["type"] == "session.state_changed"
        assert delta1["event"]["payload"]["to_state"] == "BUTTON_PRESSED"

        # Read the session.started
        delta2_raw = websocket.receive_text()
        delta2 = json.loads(delta2_raw)
        assert delta2["type"] == "delta"
        assert delta2["event"]["type"] == "session.started"

        # Read the state_changed for VISITOR_MODE
        delta3_raw = websocket.receive_text()
        delta3 = json.loads(delta3_raw)
        assert delta3["type"] == "delta"
        assert delta3["event"]["type"] == "session.state_changed"
        assert delta3["event"]["payload"]["to_state"] == "VISITOR_MODE"


def _receive_delta(websocket, event_type: str) -> dict:
    while True:
        data = json.loads(websocket.receive_text())
        if data.get("type") == "delta" and data.get("event", {}).get("type") == event_type:
            return data["event"]


def test_doorpad_ring_uses_touch_entry_and_effect_event() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        json.loads(websocket.receive_text())  # snapshot
        response = client.post("/doorpad/ring")

        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["effect"]["status"] == "unavailable"
        assert body["session"]["state"] == "VISITOR_MODE"

        changed = _receive_delta(websocket, "session.state_changed")
        started = _receive_delta(websocket, "session.started")
        effect = _receive_delta(websocket, "door.effect_play")

        assert changed["payload"]["trigger"] == "doorpad.touch_ring"
        assert started["payload"]["entry"] == "touch"
        assert effect["payload"]["effect_id"] == state.config.doorpad_effect_id


def test_doorpad_end_is_a_real_local_session_action() -> None:
    client = TestClient(app)
    assert client.post("/doorpad/ring").json()["accepted"] is True

    response = client.post("/doorpad/session/end")

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["session"]["state"] == "SESSION_END"


def test_admin_can_answer_decline_or_end_a_live_ring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOR_API_SOCIAL_ADMIN_TOKEN", "owner-token")
    state.shutdown()
    state.__init__()
    state.startup()
    client = TestClient(app)
    headers = {"Authorization": "Bearer owner-token"}

    client.post("/doorpad/ring")
    assert state.machine.transition(SessionState.RINGING, "test:ring")
    answered = client.post("/admin/session/answer", headers=headers)
    assert answered.status_code == 200
    assert answered.json()["session"]["state"] == "ANSWERED"

    ended = client.post("/admin/session/end", headers=headers)
    assert ended.json()["session"]["state"] == "SESSION_END"
    client.post("/admin/session/end", headers=headers)

    client.post("/doorpad/ring")
    assert state.machine.transition(SessionState.RINGING, "test:ring")
    declined = client.post("/admin/session/cannot-answer", headers=headers)
    assert declined.status_code == 200
    assert declined.json()["session"]["state"] == "UNANSWERED_TIMEOUT"


def test_video_message_offer_starts_touch_session() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        json.loads(websocket.receive_text())  # snapshot
        response = client.post("/doorpad/video-message/offer")

        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["session"]["state"] == "VIDEO_MESSAGE_OFFERED"

        first_changed = _receive_delta(websocket, "session.state_changed")
        started = _receive_delta(websocket, "session.started")
        assert first_changed["payload"]["trigger"] == "doorpad.video_message_offer"
        assert started["payload"]["entry"] == "touch"


def test_visitor_token_requires_active_session_and_is_scoped() -> None:
    client = TestClient(app)
    missing = client.get("/visitor-token")
    assert missing.status_code == 409

    client.post("/doorpad/ring")
    response = client.get("/visitor-token")

    assert response.status_code == 200
    token = response.json()["token"]
    assert "." in token
    assert response.json()["url"].endswith(f"/visitor?token={token}")


def test_photobooth_feature_off_hides_public_endpoints() -> None:
    client = TestClient(app)
    response = client.post("/doorpad/photo-booth/capture")
    assert response.status_code == 404
    moments = client.get("/wallboard/moments")
    assert moments.status_code == 404


class _FakeGalleryClient:
    """Fake httpx.AsyncClient serving door-sync's internal gallery moments."""

    moments: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def __aenter__(self) -> _FakeGalleryClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args

    async def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeMediaResponse:
        del params, headers
        if url.endswith("/internal/gallery/moments"):
            return _FakeMediaResponse(body={"photos": self.__class__.moments})
        return _FakeMediaResponse(body={})


def _enable_photobooth(monkeypatch: pytest.MonkeyPatch) -> None:
    # monkeypatch.setenv auto-reverts so photobooth stays off for other tests.
    monkeypatch.setenv("FEATURE_PHOTOBOOTH", "true")
    state.shutdown()
    state.__init__()
    state.startup()


def test_visitor_collage_requires_admin_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    # The collage is private all year and only revealed via the owner-only
    # /reveal page, so the endpoint is admin-namespaced + owner-gated. Without a
    # configured token it already fails closed with 503 (see the fail-closed
    # test); once configured, a valid bearer token is required.
    headers = _enable_admin(monkeypatch)

    denied = TestClient(app).get("/admin/visitor-collage")
    assert denied.status_code == 401

    wrong = TestClient(app).get("/admin/visitor-collage", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401

    allowed = TestClient(app).get("/admin/visitor-collage", headers=headers)
    assert allowed.status_code == 200
    assert "stats" in allowed.json()


def test_visitor_collage_returns_count_only_stats_when_photobooth_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Photobooth is off in the default fixture: stats still work, photos do not.
    headers = _enable_admin(monkeypatch)
    state.social_service.create_checkin(
        person_id="prs_alex", label="Alex", ip="10.0.0.1", session_token="s1", trace_id="t"
    )
    state.social_service.create_checkin(
        person_id=None, label="guest", ip="10.0.0.2", session_token="s2", trace_id="t"
    )

    response = TestClient(app).get("/admin/visitor-collage", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["stats"]["total_checkins"] == 2
    assert body["stats"]["unique_visitors"] == 2  # one enrolled + one guest
    assert body["stats"]["most_frequent"] == {"label": "Alex", "count": 1}
    assert body["photos"] == []


def test_visitor_collage_empty_case(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = _enable_admin(monkeypatch)
    response = TestClient(app).get("/admin/visitor-collage", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["stats"]["total_checkins"] == 0
    assert body["stats"]["most_frequent"] is None
    assert body["stats"]["first_checkin_at"] is None
    assert body["photos"] == []


def test_visitor_collage_returns_only_owner_approved_checkin_photos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = _enable_admin(monkeypatch)
    _enable_photobooth(monkeypatch)
    monkeypatch.setattr("door_api.app.httpx.AsyncClient", _FakeGalleryClient)

    # An approved photo check-in, an un-approved photo check-in, and a photoless one.
    state.social_service.create_checkin(
        person_id=None,
        label="Approved Guest",
        photo_recording_id="rec_ok",
        ip="10.0.0.1",
        session_token="s1",
        trace_id="t",
    )
    state.social_service.create_checkin(
        person_id=None,
        label="Private Guest",
        photo_recording_id="rec_private",
        ip="10.0.0.2",
        session_token="s2",
        trace_id="t",
    )
    state.social_service.create_checkin(
        person_id=None, label="No Photo", ip="10.0.0.3", session_token="s3", trace_id="t"
    )

    # Only rec_ok is owner-approved + wallboard-eligible in the gallery.
    _FakeGalleryClient.moments = [
        {
            "recording_id": "rec_ok",
            "status": "approved",
            "gallery_thumbnail_path": "gallery/albums/2026-07/thumbnails/rec_ok.jpg",
        },
        # rec_private is intentionally absent from the approved moments feed.
    ]

    response = TestClient(app).get("/admin/visitor-collage", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["stats"]["total_checkins"] == 3
    # rec_private (not approved) and the photoless check-in are excluded.
    assert [p["recording_id"] for p in body["photos"]] == ["rec_ok"]
    photo = body["photos"][0]
    assert photo["label"] == "Approved Guest"
    assert photo["thumbnail_path"] == "gallery/albums/2026-07/thumbnails/rec_ok.jpg"


def test_visitor_collage_degrades_to_stats_when_gallery_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = _enable_admin(monkeypatch)
    _enable_photobooth(monkeypatch)

    class _FailingGalleryClient(_FakeGalleryClient):
        async def get(self, url: str, **kwargs: Any) -> _FakeMediaResponse:
            del url, kwargs
            raise RuntimeError("gallery down")

    monkeypatch.setattr("door_api.app.httpx.AsyncClient", _FailingGalleryClient)
    state.social_service.create_checkin(
        person_id=None,
        label="Guest",
        photo_recording_id="rec_ok",
        ip="10.0.0.1",
        session_token="s1",
        trace_id="t",
    )

    response = TestClient(app).get("/admin/visitor-collage", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["stats"]["total_checkins"] == 1
    assert body["photos"] == []  # never leak when the private gallery is unreachable


def test_contract_button_event_sets_cached_profile_snapshot() -> None:
    events = EventFactory(SimClock())
    button = events.make(
        "door.button_pressed",
        {
            "press_id": "00000000-0000-0000-0000-000000000001",
            "had_cached_profile": True,
            "profile_id": "blue_wave",
        },
    )

    accepted = state.handle_contract_event(button)

    assert accepted is True
    snapshot = state.snapshot_response()["session"]
    assert snapshot["state"] == "VISITOR_MODE"
    assert snapshot["had_cached_profile"] is True
    assert snapshot["profile_id"] == "blue_wave"
