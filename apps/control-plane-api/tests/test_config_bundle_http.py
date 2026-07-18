"""Config distribution: versioned bundle, checksum, and the secret-free guarantee."""

from __future__ import annotations

from doorboard_config import ConfigBundle, assert_secret_free
from fastapi.testclient import TestClient

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


def _issue_config_token(client: TestClient) -> str:
    resp = client.post(
        "/admin/tokens", json={"scope": "config", "door_id": "primary"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 201
    return resp.json()["token"]


def test_get_config_auto_creates_version_one(client: TestClient) -> None:
    token = _issue_config_token(client)
    resp = client.get("/config/door/primary", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["door_id"] == "primary"
    assert body["version"] == 1
    assert "checksum" in body


def test_get_config_requires_config_scope(client: TestClient) -> None:
    resp = client.get("/config/door/primary")
    assert resp.status_code == 401


def test_config_token_cannot_read_another_doors_bundle(client: TestClient) -> None:
    token = _issue_config_token(client)

    response = client.get(
        "/config/door/secondary",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_ingest_token_cannot_read_config(client: TestClient) -> None:
    resp = client.post(
        "/admin/tokens", json={"scope": "ingest", "door_id": "primary"}, headers=ADMIN_HEADERS
    )
    ingest_token = resp.json()["token"]
    resp2 = client.get("/config/door/primary", headers={"Authorization": f"Bearer {ingest_token}"})
    assert resp2.status_code == 401


def test_admin_update_bumps_version_and_checksum(client: TestClient) -> None:
    token = _issue_config_token(client)
    initial = client.get(
        "/config/door/primary", headers={"Authorization": f"Bearer {token}"}
    ).json()

    updated = client.put(
        "/config/door/primary",
        json={"vision_mode": "hardware", "single_camera_mode": False},
        headers=ADMIN_HEADERS,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["version"] == 2
    assert body["checksum"] != initial["checksum"]
    assert body["settings"]["vision_mode"] == "hardware"

    fetched_again = client.get(
        "/config/door/primary", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert fetched_again["version"] == 2
    assert fetched_again["settings"]["vision_mode"] == "hardware"


def test_served_bundle_is_secret_free_by_denylist_scan(client: TestClient) -> None:
    token = _issue_config_token(client)
    resp = client.get("/config/door/primary", headers={"Authorization": f"Bearer {token}"})
    body = resp.json()
    # Reconstruct the same ConfigBundle the endpoint served and scan it —
    # this is the acceptance criterion's "greps bundle contents against a
    # denylist" check, run against the real HTTP response body.
    bundle = ConfigBundle.model_validate(body)
    assert_secret_free(bundle)  # must not raise
