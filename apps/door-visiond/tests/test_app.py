"""HTTP surface for door-visiond (health, metrics, enroll/unenroll, privacy)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "door-visiond"
    assert data["mode"] == "mock"
    assert data["privacy_enabled"] is False


def test_metrics(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "door_visiond_uptime_s" in resp.text
    assert "door_visiond_cache_hit_rate" in resp.text


def test_current_visitor_empty_is_204(client: TestClient) -> None:
    resp = client.get("/current-visitor")
    assert resp.status_code == 204


def _enroll(client: TestClient) -> str:
    files = [("images", ("a.bin", b"alex-photo-bytes", "application/octet-stream"))]
    data = {
        "display_name": "Alex",
        "consent_version": "v1",
        "consent_confirmed": "true",
        "profile_id": "blue_wave",
        "color": "#0000ff",
    }
    resp = client.post("/enroll", data=data, files=files)
    assert resp.status_code == 201, resp.text
    return resp.json()["person_id"]


def test_enroll_and_unenroll(client: TestClient) -> None:
    person_id = _enroll(client)
    assert person_id.startswith("prs_")

    resp = client.post("/unenroll", json={"person_id": person_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] is True
    assert body["archive_purge"] == "queued"


def test_enroll_stale_consent_is_409(client: TestClient) -> None:
    files = [("images", ("a.bin", b"alex-photo-bytes", "application/octet-stream"))]
    data = {
        "display_name": "Alex",
        "consent_version": "v0",
        "consent_confirmed": "true",
        "profile_id": "blue_wave",
        "color": "#0000ff",
    }
    resp = client.post("/enroll", data=data, files=files)
    assert resp.status_code == 409


def test_privacy_mode_toggle_and_enroll_block(client: TestClient) -> None:
    resp = client.post("/privacy-mode", json={"enabled": True, "changed_by": "admin"})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    health = client.get("/health").json()
    assert health["privacy_enabled"] is True
    assert health["mode"] == "disabled"

    # Enrollment blocked while privacy active.
    files = [("images", ("a.bin", b"alex-photo-bytes", "application/octet-stream"))]
    data = {
        "display_name": "Alex",
        "consent_version": "v1",
        "consent_confirmed": "true",
        "profile_id": "blue_wave",
        "color": "#0000ff",
    }
    resp = client.post("/enroll", data=data, files=files)
    assert resp.status_code == 409


def test_privacy_mode_invalid_changed_by_is_422(client: TestClient) -> None:
    resp = client.post("/privacy-mode", json={"enabled": True, "changed_by": "hacker"})
    assert resp.status_code == 422
