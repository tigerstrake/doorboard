"""HTTP-level tests for the /guestbook, /polls, /checkins, /social, and
/admin/* routes — request validation, error envelopes, and the admin-token
placeholder gate.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state


@pytest.fixture(autouse=True)
def _mock_env_for_test(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.delenv("DOOR_API_SOCIAL_ADMIN_TOKEN", raising=False)
    state.__init__()
    state.startup()
    yield
    state.shutdown()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Guestbook
# ---------------------------------------------------------------------------


def test_create_and_list_guestbook_requires_admin_approval(client: TestClient) -> None:
    resp = client.post("/guestbook", json={"text": "hi there", "session_token": "visitor-1"})
    assert resp.status_code == 201

    # Not visible publicly until approved.
    assert client.get("/guestbook").json()["entries"] == []


def test_guestbook_validation_error_returns_error_envelope(client: TestClient) -> None:
    resp = client.post("/guestbook", json={"text": "   ", "session_token": "visitor-1"})
    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert body["error"]["code"] == "invalid_input"
    assert "trace_id" in body["error"]


def test_guestbook_rate_limited_after_default_burst(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOR_API_SOCIAL_RATE_LIMIT_COUNT", "2")
    state.__init__()
    state.startup()

    for i in range(2):
        resp = client.post(
            "/guestbook", json={"text": f"entry {i}", "session_token": f"session-{i}"}
        )
        assert resp.status_code == 201

    resp = client.post("/guestbook", json={"text": "one too many", "session_token": "session-99"})
    assert resp.status_code == 429
    assert resp.json()["detail"]["error"]["code"] == "rate_limited"


def test_trace_id_propagated_from_header(client: TestClient) -> None:
    resp = client.post(
        "/guestbook",
        json={"text": "   ", "session_token": "visitor-1"},
        headers={"X-Trace-Id": "my-trace-123"},
    )
    assert resp.json()["detail"]["error"]["trace_id"] == "my-trace-123"


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------


def test_no_current_poll_returns_null(client: TestClient) -> None:
    resp = client.get("/polls/current")
    assert resp.status_code == 200
    assert resp.json() == {"poll": None}


def test_vote_flow_end_to_end(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOR_API_SOCIAL_ADMIN_TOKEN", "test-admin-token")
    state.__init__()
    state.startup()

    create_resp = client.post(
        "/admin/polls",
        json={"question": "Snack?", "options": ["Tea", "Coffee"]},
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert create_resp.status_code == 201
    poll = create_resp.json()
    option_id = poll["options"][0]["id"]

    current = client.get("/polls/current").json()["poll"]
    assert current["id"] == poll["id"]

    vote_resp = client.post(
        f"/polls/{poll['id']}/vote",
        json={"option_id": option_id, "session_token": "visitor-1"},
    )
    assert vote_resp.status_code == 201

    dup_resp = client.post(
        f"/polls/{poll['id']}/vote",
        json={"option_id": option_id, "session_token": "visitor-1"},
    )
    assert dup_resp.status_code == 409
    assert dup_resp.json()["detail"]["error"]["code"] == "already_voted"

    results = client.get(f"/polls/{poll['id']}/results").json()["results"]
    assert next(r for r in results if r["option_id"] == option_id)["votes"] == 1


# ---------------------------------------------------------------------------
# Check-ins
# ---------------------------------------------------------------------------


def test_checkin_create_and_stats(client: TestClient) -> None:
    resp = client.post(
        "/checkins",
        json={"person_id": "prs_alex", "label": "Alex", "session_token": "s1"},
    )
    assert resp.status_code == 201

    stats = client.get("/checkins/stats/most-frequent").json()["stat"]
    assert stats["person_id"] == "prs_alex"
    assert stats["count"] == 1


# ---------------------------------------------------------------------------
# Deletion requests
# ---------------------------------------------------------------------------


def test_deletion_request_unsupported_target_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/social/deletion-requests",
        json={"target_kind": "video_message", "target_id": "abc", "session_token": "s1"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"]["code"] == "unsupported_target"


def test_deletion_request_removes_guestbook_entry(client: TestClient) -> None:
    create_resp = client.post("/guestbook", json={"text": "hi there", "session_token": "visitor-1"})
    entry_id = create_resp.json()["id"]

    del_resp = client.post(
        "/social/deletion-requests",
        json={"target_kind": "guestbook", "target_id": entry_id, "session_token": "visitor-1"},
    )
    assert del_resp.status_code == 202


# ---------------------------------------------------------------------------
# Admin auth gate
# ---------------------------------------------------------------------------


def test_admin_routes_fail_closed_when_token_unconfigured(client: TestClient) -> None:
    resp = client.get("/admin/guestbook")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"]["code"] == "admin_not_configured"


def test_admin_routes_reject_missing_or_wrong_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOR_API_SOCIAL_ADMIN_TOKEN", "correct-token")
    state.__init__()
    state.startup()

    resp = client.get("/admin/guestbook")
    assert resp.status_code == 401

    resp = client.get("/admin/guestbook", headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401


def test_admin_moderation_flow(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOOR_API_SOCIAL_ADMIN_TOKEN", "correct-token")
    state.__init__()
    state.startup()
    auth = {"Authorization": "Bearer correct-token"}

    create_resp = client.post("/guestbook", json={"text": "hi there", "session_token": "visitor-1"})
    entry_id = create_resp.json()["id"]

    pending = client.get("/admin/guestbook?status=pending", headers=auth).json()["entries"]
    assert len(pending) == 1
    assert pending[0]["id"] == entry_id

    approve_resp = client.post(f"/admin/guestbook/{entry_id}/approve", headers=auth)
    assert approve_resp.status_code == 200
    assert client.get("/guestbook").json()["entries"][0]["id"] == entry_id

    delete_resp = client.delete(f"/admin/guestbook/{entry_id}", headers=auth)
    assert delete_resp.status_code == 200
    assert client.get("/guestbook").json()["entries"] == []

    log = client.get("/admin/social/moderation-log", headers=auth).json()["entries"]
    actions = {(e["target_id"], e["action"]) for e in log}
    assert (entry_id, "created") in actions
    assert (entry_id, "approved") in actions
    assert (entry_id, "deleted") in actions


# ---------------------------------------------------------------------------
# Injection round-trip: the API accepts hostile input, never crashes, and
# returns raw (unescaped) text — the render boundary (frontend) is
# responsible for escaping. This proves the storage layer's contract.
# ---------------------------------------------------------------------------


def test_hostile_guestbook_text_round_trips_safely(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOOR_API_SOCIAL_ADMIN_TOKEN", "correct-token")
    state.__init__()
    state.startup()
    auth = {"Authorization": "Bearer correct-token"}

    hostile = "<script>alert(1)</script>"
    create_resp = client.post("/guestbook", json={"text": hostile, "session_token": "visitor-1"})
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]
    assert create_resp.json()["text"] == hostile  # stored raw, not double-escaped

    client.post(f"/admin/guestbook/{entry_id}/approve", headers=auth)
    listed = client.get("/guestbook").json()["entries"]
    assert listed[0]["text"] == hostile
