"""Person-data purge (ADR-0009 §3.4): idempotent, queue-safe repeatable calls."""

from __future__ import annotations

from control_plane_api.models import EventRow, SocialItemRow
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from .factories import build_event

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


def _ingest_token(client: TestClient) -> str:
    resp = client.post(
        "/admin/tokens", json={"scope": "ingest", "door_id": "primary"}, headers=ADMIN_HEADERS
    )
    return resp.json()["token"]


def test_purge_deletes_events_and_checkins_for_the_person(
    client: TestClient, session_factory
) -> None:
    token = _ingest_token(client)
    person_id = "prs_purge_target"
    events = [
        build_event("vision.identity_stable", payload_overrides={"person_id": person_id}),
        build_event("vision.identity_expired", payload_overrides={"person_id": person_id}),
        build_event(
            "social.checkin_created", payload_overrides={"person_id": person_id, "label": "Alex"}
        ),
        build_event("vision.identity_stable", payload_overrides={"person_id": "someone_else"}),
    ]
    resp = client.post(
        "/ingest",
        json={"batch_id": "b1", "events": events},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert [r["status"] for r in resp.json()["results"]] == ["stored"] * 4
    checkin_id = events[2]["payload"]["checkin_id"]

    purge_resp = client.delete(
        f"/people/{person_id}/events", headers={"Authorization": f"Bearer {token}"}
    )
    assert purge_resp.status_code == 200
    body = purge_resp.json()
    assert body["events_deleted"] == 3
    assert body["checkins_deleted"] == 1

    with session_factory() as session:
        remaining_for_person = session.execute(
            select(func.count()).select_from(EventRow).where(EventRow.person_id == person_id)
        ).scalar_one()
        remaining_other = session.execute(
            select(func.count()).select_from(EventRow).where(EventRow.person_id == "someone_else")
        ).scalar_one()
        checkin_row = session.get(SocialItemRow, ("checkin", checkin_id))
    assert remaining_for_person == 0
    assert remaining_other == 1
    assert checkin_row.status == "deleted"
    assert checkin_row.deleted_reason == "purge"


def test_purge_is_idempotent_when_called_repeatedly(client: TestClient) -> None:
    token = _ingest_token(client)
    person_id = "prs_repeat_purge"
    event = build_event("vision.identity_stable", payload_overrides={"person_id": person_id})
    client.post(
        "/ingest",
        json={"batch_id": "b1", "events": [event]},
        headers={"Authorization": f"Bearer {token}"},
    )

    first = client.delete(
        f"/people/{person_id}/events", headers={"Authorization": f"Bearer {token}"}
    )
    second = client.delete(
        f"/people/{person_id}/events", headers={"Authorization": f"Bearer {token}"}
    )
    third = client.delete(
        f"/people/{person_id}/events", headers={"Authorization": f"Bearer {token}"}
    )

    assert first.status_code == second.status_code == third.status_code == 200
    assert first.json()["events_deleted"] == 1
    assert second.json()["events_deleted"] == 0
    assert third.json()["events_deleted"] == 0


def test_purge_of_a_person_with_no_data_succeeds_with_zero_counts(client: TestClient) -> None:
    token = _ingest_token(client)
    resp = client.delete(
        "/people/prs_never_seen/events", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "person_id": "prs_never_seen",
        "events_deleted": 0,
        "checkins_deleted": 0,
        "status": "purged",
    }
