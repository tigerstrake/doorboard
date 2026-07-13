"""Service-token auth over HTTP: issue, use, revoke-takes-effect-immediately."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .factories import build_event

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


def _issue(client: TestClient, *, scope: str = "ingest", door_id: str = "primary") -> str:
    resp = client.post(
        "/admin/tokens", json={"scope": scope, "door_id": door_id}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def test_ingest_requires_bearer_token(client: TestClient) -> None:
    resp = client.post("/ingest", json={"batch_id": "b1", "events": []})
    assert resp.status_code == 401


def test_ingest_rejects_unknown_token(client: TestClient) -> None:
    resp = client.post(
        "/ingest",
        json={"batch_id": "b1", "events": []},
        headers={"Authorization": "Bearer dbt_unknown.secret"},
    )
    assert resp.status_code == 401


def test_valid_ingest_token_is_accepted(client: TestClient) -> None:
    token = _issue(client, scope="ingest")
    resp = client.post(
        "/ingest",
        json={"batch_id": "b1", "events": [build_event("system.service_health")]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["status"] == "stored"


def test_ingest_token_cannot_submit_another_doors_event(client: TestClient) -> None:
    token = _issue(client, scope="ingest", door_id="secondary")
    event = build_event("system.service_health")

    response = client.post(
        "/ingest",
        json={"batch_id": "cross-door", "events": [event]},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_ingest_batch_size_is_bounded(client: TestClient) -> None:
    token = _issue(client, scope="ingest")
    event = build_event("system.service_health")

    response = client.post(
        "/ingest",
        json={"batch_id": "too-many", "events": [event] * 201},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


def test_http_request_body_size_is_bounded(client: TestClient) -> None:
    response = client.post(
        "/ingest",
        content=b"x" * (1024 * 1024 + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_wrong_scope_token_is_rejected(client: TestClient) -> None:
    upload_token = _issue(client, scope="upload")
    resp = client.post(
        "/ingest",
        json={"batch_id": "b1", "events": []},
        headers={"Authorization": f"Bearer {upload_token}"},
    )
    assert resp.status_code == 401


def test_revoked_token_rejected_on_the_very_next_request(client: TestClient) -> None:
    resp = client.post(
        "/admin/tokens", json={"scope": "ingest", "door_id": "primary"}, headers=ADMIN_HEADERS
    )
    token_id = resp.json()["token_id"]
    token = resp.json()["token"]

    ok = client.post(
        "/ingest",
        json={"batch_id": "b1", "events": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ok.status_code == 200

    revoke_resp = client.delete(f"/admin/tokens/{token_id}", headers=ADMIN_HEADERS)
    assert revoke_resp.status_code == 200

    after_revoke = client.post(
        "/ingest",
        json={"batch_id": "b2", "events": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert after_revoke.status_code == 401


def test_revoking_an_already_revoked_token_reports_not_found(client: TestClient) -> None:
    resp = client.post(
        "/admin/tokens", json={"scope": "ingest", "door_id": "primary"}, headers=ADMIN_HEADERS
    )
    token_id = resp.json()["token_id"]
    first = client.delete(f"/admin/tokens/{token_id}", headers=ADMIN_HEADERS)
    second = client.delete(f"/admin/tokens/{token_id}", headers=ADMIN_HEADERS)
    assert first.status_code == 200
    assert second.status_code == 404


def test_admin_endpoints_require_admin_token(client: TestClient) -> None:
    resp = client.get("/admin/tokens")
    assert resp.status_code == 401
    resp2 = client.get("/admin/tokens", headers={"Authorization": "Bearer wrong"})
    assert resp2.status_code == 401
