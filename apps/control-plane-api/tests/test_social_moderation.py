"""Social moderation panel + `social.deletion_requested` propagation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .factories import build_event

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


def _ingest_token(client: TestClient) -> str:
    resp = client.post(
        "/admin/tokens", json={"scope": "ingest", "door_id": "primary"}, headers=ADMIN_HEADERS
    )
    return resp.json()["token"]


def _ingest(client: TestClient, token: str, events: list[dict]) -> dict:
    resp = client.post(
        "/ingest",
        json={"batch_id": "b1", "events": events},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_guestbook_entry_appears_in_admin_panel(client: TestClient) -> None:
    token = _ingest_token(client)
    entry = build_event("social.guestbook_entry_created", payload_overrides={"text": "hi there"})
    _ingest(client, token, [entry])

    resp = client.get("/admin/social/guestbook", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["text"] == "hi there"
    assert items[0]["status"] == "active"


def test_admin_can_moderate_delete_a_guestbook_entry(client: TestClient) -> None:
    token = _ingest_token(client)
    entry = build_event("social.guestbook_entry_created")
    _ingest(client, token, [entry])
    item_id = entry["payload"]["entry_id"]

    del_resp = client.delete(f"/admin/social/guestbook/{item_id}", headers=ADMIN_HEADERS)
    assert del_resp.status_code == 200

    listed = client.get("/admin/social/guestbook", headers=ADMIN_HEADERS).json()["items"]
    assert listed[0]["status"] == "deleted"
    assert listed[0]["deleted_reason"] == "moderation"


def test_visitor_deletion_request_marks_guestbook_entry_deleted(client: TestClient) -> None:
    token = _ingest_token(client)
    entry = build_event("social.guestbook_entry_created")
    entry_id = entry["payload"]["entry_id"]
    deletion = build_event(
        "social.deletion_requested",
        payload_overrides={"target_kind": "guestbook", "target_id": entry_id},
    )
    result = _ingest(client, token, [entry, deletion])
    assert [r["status"] for r in result["results"]] == ["stored", "stored"]

    listed = client.get("/admin/social/guestbook", headers=ADMIN_HEADERS).json()["items"]
    assert listed[0]["status"] == "deleted"
    assert listed[0]["deleted_reason"] == "deletion_requested"


def test_deletion_request_for_unknown_item_is_a_harmless_no_op(client: TestClient) -> None:
    token = _ingest_token(client)
    deletion = build_event(
        "social.deletion_requested",
        payload_overrides={"target_kind": "checkin", "target_id": "does-not-exist"},
    )
    result = _ingest(client, token, [deletion])
    assert result["results"][0]["status"] == "stored"


def test_deletion_request_for_video_message_tombstones_media_mirror(client: TestClient) -> None:
    token = _ingest_token(client)
    recording = build_event("media.recording_started", payload_overrides={"kind": "video_message"})
    recording_id = recording["payload"]["recording_id"]
    deletion = build_event(
        "social.deletion_requested",
        payload_overrides={"target_kind": "video_message", "target_id": recording_id},
    )
    _ingest(client, token, [recording, deletion])
    # No direct read endpoint for media_mirror in this brief's scope; verified
    # at the unit level in test_deletion.py against the ORM row directly.


def test_checkin_visible_in_admin_panel(client: TestClient) -> None:
    token = _ingest_token(client)
    checkin = build_event("social.checkin_created", payload_overrides={"label": "Alex"})
    _ingest(client, token, [checkin])

    resp = client.get("/admin/social/checkins", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0]["label"] == "Alex"


def test_admin_delete_checkin_not_found_returns_404(client: TestClient) -> None:
    resp = client.delete("/admin/social/checkins/does-not-exist", headers=ADMIN_HEADERS)
    assert resp.status_code == 404
