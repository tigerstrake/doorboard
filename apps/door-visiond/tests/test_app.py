"""HTTP surface for door-visiond (health, metrics, enroll/unenroll, privacy)."""

from __future__ import annotations

import json
from typing import Any, cast
from urllib.parse import parse_qs

import pytest
from door_visiond.app import app
from door_visiond.settings import Settings, override_settings, reset_settings
from fastapi.testclient import TestClient

from .conftest import CONSENT_VERSION


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "door-visiond"
    assert data["mode"] == "mock"
    assert data["privacy_enabled"] is False


def test_consent_endpoint_returns_canonical_statement_verbatim(
    client: TestClient,
    ssd_settings: Settings,
) -> None:
    response = client.get("/consent")
    assert response.status_code == 200
    path = ssd_settings.consent_statement_path
    assert path is not None
    expected = path.read_text(encoding="utf-8")
    assert response.json() == {"text": expected, "version": CONSENT_VERSION}


def test_metrics(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "door_visiond_uptime_s" in resp.text
    assert "door_visiond_cache_hit_rate" in resp.text


def test_admin_routes_fail_closed_without_configured_token(client: TestClient) -> None:
    cfg = cast(Any, client.app).state.cfg
    cfg.admin_token = ""

    assert client.get("/people").status_code == 503


def test_current_visitor_empty_is_204(client: TestClient) -> None:
    resp = client.get("/current-visitor")
    assert resp.status_code == 204


def _enroll(client: TestClient) -> str:
    files = [("images", ("a.bin", b"alex-photo-bytes", "application/octet-stream"))]
    data = {
        "display_name": "Alex",
        "consent_version": CONSENT_VERSION,
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
        "consent_version": CONSENT_VERSION,
        "consent_confirmed": "true",
        "profile_id": "blue_wave",
        "color": "#0000ff",
    }
    resp = client.post("/enroll", data=data, files=files)
    assert resp.status_code == 409


def test_privacy_mode_invalid_changed_by_is_422(client: TestClient) -> None:
    resp = client.post("/privacy-mode", json={"enabled": True, "changed_by": "hacker"})
    assert resp.status_code == 422


def test_list_people(client: TestClient) -> None:
    # Initially empty list of enrolled people.
    resp = client.get("/people")
    assert resp.status_code == 200
    assert resp.json() == []

    # Enroll one person.
    person_id = _enroll(client)

    # Check that they appear in the list.
    resp = client.get("/people")
    assert resp.status_code == 200
    people = resp.json()
    assert len(people) == 1
    assert people[0]["person_id"] == person_id
    assert people[0]["display_name"] == "Alex"
    assert people[0]["profile_id"] == "blue_wave"
    assert people[0]["color"] == "#0000ff"
    assert "consent_at" in people[0]

    # Clean up.
    client.post("/unenroll", json={"person_id": person_id})


def test_get_consent(client: TestClient) -> None:
    resp = client.get("/consent")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
    assert "text" in data
    assert data["version"] == CONSENT_VERSION
    assert "consent" in data["text"].lower()


# -- remote-enrollment invite endpoints (ADR-0016 §4) -----------------------

_RELAY_ADMIN_ROUTES = [
    ("post", "/invites"),
    ("get", "/invites"),
    ("post", "/invites/inv_abc/revoke"),
    ("get", "/relay-status"),
    ("post", "/relay-key/rotate"),
]


@pytest.mark.parametrize(("method", "path"), _RELAY_ADMIN_ROUTES)
def test_relay_routes_require_admin_auth(ssd_settings: Settings, method: str, path: str) -> None:
    """Nothing enrollment-related may be reachable without the admin token."""
    override_settings(ssd_settings)
    try:
        with TestClient(app) as anon:
            kwargs = {"json": {}} if method == "post" else {}
            resp = getattr(anon, method)(path, **kwargs)
        assert resp.status_code == 401, f"{method.upper()} {path} was reachable anonymously"
    finally:
        reset_settings()


def test_create_invite_returns_a_url_with_a_pinned_fingerprint(client: TestClient) -> None:
    resp = client.post("/invites", json={"label": "Tiger's phone"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["invite_id"].startswith("inv_")
    # Secret and fingerprint live in the fragment now (ADR-0043 §2); the path is just the id.
    before_fragment, fragment = body["url"].split("#", 1)
    frag = parse_qs(fragment)
    assert frag["k"] == [body["door_key_fingerprint"]]
    assert before_fragment.rsplit("/e/", 1)[1] == body["invite_id"]


def test_listed_invites_never_expose_the_secret(client: TestClient) -> None:
    created = client.post("/invites", json={"label": "phone"}).json()
    secret = parse_qs(created["url"].split("#", 1)[1])["s"][0]

    listed = client.get("/invites").json()
    assert [i["invite_id"] for i in listed] == [created["invite_id"]]
    assert listed[0]["status"] == "open"
    assert secret not in json.dumps(listed)


def test_revoking_an_invite_closes_it(client: TestClient) -> None:
    created = client.post("/invites", json={}).json()
    assert client.post(f"/invites/{created['invite_id']}/revoke").json() == {"revoked": True}
    assert client.get("/invites").json() == []
    closed = client.get("/invites", params={"include_closed": True}).json()
    assert closed[0]["status"] == "revoked"
    # Revoking twice is a no-op, not an error.
    assert client.post(f"/invites/{created['invite_id']}/revoke").json() == {"revoked": False}


def test_relay_status_reports_unconfigured_by_default(client: TestClient) -> None:
    assert client.get("/relay-status").json() == {"configured": False, "status": "disabled"}


def test_rotating_the_relay_key_changes_the_fingerprint(client: TestClient) -> None:
    before = client.post("/invites", json={}).json()["door_key_fingerprint"]
    rotated = client.post("/relay-key/rotate").json()
    assert rotated["fingerprint"] != before
    assert rotated["door_key_id"].startswith("dky_")
    after = client.post("/invites", json={}).json()["door_key_fingerprint"]
    assert after == rotated["fingerprint"]
