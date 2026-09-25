"""The DoorPad's playback link is a projection with a capability, not door-media's row.

``GET /doorpad/video-message/latest`` is a public doorpad route. It returned door-media's
raw recording row — including the SSD file ``path`` and the ``sha256`` — and a playback
URL that anyone who could guess a recording id and a session id could fetch. It now
returns only what the review screen needs, and the URL carries a token bound to one
recording, one session and a deadline, minted from door-media's admin token in a way
door-media can verify without that token ever leaving either service.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import _mint_playback_token, app, state  # noqa: E402

ROW = {
    "recording_id": "rec-42",
    "kind": "video_message",
    "path": "/mnt/ssd/recordings/rec-42.mp4",
    "sha256": "deadbeef" * 8,
    "duration_s": 12.5,
    "finalized_at_utc": "2026-09-24T12:00:00Z",
    "thumbnail_path": "/mnt/ssd/recordings/rec-42.jpg",
    "sync_status": "pending",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_MEDIA_ADMIN_TOKEN", "media-admin-token")
    state.__init__()
    state.startup()
    yield
    state.shutdown()


class _Response:
    def __init__(self, body: Any) -> None:
        self._body = body
        self.status_code = 200

    def json(self) -> Any:
        return self._body

    def raise_for_status(self) -> None:
        return None


class _FakeMedia:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def __aenter__(self) -> _FakeMedia:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args

    async def get(self, url: str, **kwargs: Any) -> _Response:
        del kwargs
        assert url.endswith("/recordings")
        return _Response({"recordings": [{**ROW, "session_id": str(state.machine.session_id)}]})


def _reference_token(admin_token: str, recording_id: str, session_id: str, exp: int) -> str:
    # The algorithm doorboard_auth.playback_tokens specifies, written out independently.
    key = hashlib.sha256(b"doorboard-playback:" + admin_token.encode()).digest()
    sig = hmac.new(key, f"{recording_id}|{session_id}|{exp}".encode(), hashlib.sha256).digest()
    return f"{exp}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def test_the_response_is_a_projection_with_a_verifiable_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("door_api.app.httpx.AsyncClient", _FakeMedia)
    client = TestClient(app)
    assert client.post("/doorpad/ring").json()["accepted"] is True
    session_id = str(state.machine.session_id)

    before = int(time.time())
    response = client.get("/doorpad/video-message/latest")
    after = int(time.time())

    assert response.status_code == 200
    recording = response.json()["recording"]
    assert set(recording) == {"recording_id", "playback_url", "duration_s", "finalized_at_utc"}
    assert recording["recording_id"] == "rec-42"
    assert recording["duration_s"] == 12.5
    assert recording["finalized_at_utc"] == "2026-09-24T12:00:00Z"
    # Nothing from the SSD leaks: not as a key, not as a value.
    assert "path" not in response.text
    assert "sha256" not in response.text
    assert ROW["sha256"] not in response.text

    url = recording["playback_url"]
    assert url.startswith(f"{state.config.media_public_base_url}/recordings/rec-42/file?")
    assert f"session_id={session_id}&token=" in url
    token = url.split("&token=", 1)[1]
    expires_at = int(token.split(".", 1)[0])
    assert before + 300 <= expires_at <= after + 300
    assert token == _reference_token("media-admin-token", "rec-42", session_id, expires_at)
    assert token == _mint_playback_token(
        "media-admin-token", recording_id="rec-42", session_id=session_id, expires_at=expires_at
    )


def test_a_different_recording_session_or_deadline_gets_a_different_token() -> None:
    base = _mint_playback_token("k", recording_id="r", session_id="s", expires_at=100)
    assert base != _mint_playback_token("k", recording_id="r2", session_id="s", expires_at=100)
    assert base != _mint_playback_token("k", recording_id="r", session_id="s2", expires_at=100)
    assert base != _mint_playback_token("k", recording_id="r", session_id="s", expires_at=101)
    assert base != _mint_playback_token("k2", recording_id="r", session_id="s", expires_at=100)
    assert "=" not in base, "unpadded base64url"


def test_no_session_still_means_no_recording() -> None:
    assert TestClient(app).get("/doorpad/video-message/latest").json() == {"recording": None}
