"""Social moderation panel + `social.deletion_requested` propagation."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

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


# ---------------------------------------------------------------------------
# Deletion erases, it does not flag (ADR-0005 §6, ARCHITECTURE.md §9)
# ---------------------------------------------------------------------------

SENTENCE = "I'm Sam from 3B, call me 555-0100"


def _raw_traces(session_factory) -> tuple[list[str], list[tuple]]:
    """Everything the archive still holds, read with raw SQL so the ORM cannot hide it."""
    with session_factory() as session:
        payloads = list(session.execute(text("SELECT payload::text FROM events")).scalars())
        items = list(session.execute(text("SELECT text, author_label, label FROM social_items")))
    return payloads, items


def _assert_nothing_left(session_factory, *needles: str) -> None:
    payloads, items = _raw_traces(session_factory)
    assert payloads, "the audit skeleton of the event must survive the scrub"
    for needle in needles:
        assert not any(needle in p for p in payloads), f"{needle!r} still in events.payload"
        assert not any(needle in (v or "") for row in items for v in row), (
            f"{needle!r} still in social_items"
        )
    assert all(v is None for row in items for v in row), "mirror content was not nulled"


def test_visitor_deletion_request_erases_the_text_from_mirror_and_raw_event(
    client: TestClient, session_factory
) -> None:
    token = _ingest_token(client)
    entry = build_event(
        "social.guestbook_entry_created",
        payload_overrides={"text": SENTENCE, "author_label": "Sam"},
    )
    deletion = build_event(
        "social.deletion_requested",
        payload_overrides={"target_kind": "guestbook", "target_id": entry["payload"]["entry_id"]},
    )
    _ingest(client, token, [entry])
    payloads_before, _ = _raw_traces(session_factory)
    assert any(SENTENCE in p for p in payloads_before)  # it really was archived verbatim

    _ingest(client, token, [deletion])

    _assert_nothing_left(session_factory, SENTENCE, "Sam")
    with session_factory() as session:
        skeleton = session.execute(
            text("SELECT event_id, type, payload::text FROM events WHERE type = :t"),
            {"t": "social.guestbook_entry_created"},
        ).one()
    assert skeleton.event_id == entry["event_id"]
    assert skeleton[2] == '{"scrubbed": true}'
    listed = client.get("/admin/social/guestbook", headers=ADMIN_HEADERS).json()["items"]
    assert listed[0]["status"] == "deleted"
    assert listed[0]["text"] is None


def test_moderation_delete_erases_the_text_from_mirror_and_raw_event(
    client: TestClient, session_factory
) -> None:
    token = _ingest_token(client)
    entry = build_event(
        "social.guestbook_entry_created",
        payload_overrides={"text": SENTENCE, "author_label": "Sam"},
    )
    _ingest(client, token, [entry])

    resp = client.delete(
        f"/admin/social/guestbook/{entry['payload']['entry_id']}", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200

    _assert_nothing_left(session_factory, SENTENCE, "Sam")
    listed = client.get("/admin/social/guestbook", headers=ADMIN_HEADERS).json()["items"]
    assert listed[0]["deleted_reason"] == "moderation"


def test_checkin_deletion_request_erases_the_name(client: TestClient, session_factory) -> None:
    token = _ingest_token(client)
    checkin = build_event("social.checkin_created", payload_overrides={"label": "Sam from 3B"})
    deletion = build_event(
        "social.deletion_requested",
        payload_overrides={"target_kind": "checkin", "target_id": checkin["payload"]["checkin_id"]},
    )
    _ingest(client, token, [checkin, deletion])

    _assert_nothing_left(session_factory, "Sam from 3B")


def test_a_repeated_deletion_request_is_a_no_op(client: TestClient, session_factory) -> None:
    """Deletion requests are retried by door-sync; the second must change nothing."""
    token = _ingest_token(client)
    entry = build_event("social.guestbook_entry_created", payload_overrides={"text": SENTENCE})
    target = entry["payload"]["entry_id"]
    _ingest(client, token, [entry])
    _ingest(
        client,
        token,
        [
            build_event(
                "social.deletion_requested",
                payload_overrides={"target_kind": "guestbook", "target_id": target},
            )
        ],
    )
    first = client.get("/admin/social/guestbook", headers=ADMIN_HEADERS).json()["items"][0]

    _ingest(
        client,
        token,
        [
            build_event(
                "social.deletion_requested",
                payload_overrides={"target_kind": "guestbook", "target_id": target},
            )
        ],
    )
    second = client.get("/admin/social/guestbook", headers=ADMIN_HEADERS).json()["items"][0]
    assert second == first  # deleted_at / deleted_reason preserved, nothing re-stamped
    _assert_nothing_left(session_factory, SENTENCE)
