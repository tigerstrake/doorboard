"""Presence engine over HTTP (T-504): auth boundaries, request validation,
manual-override-wins, webhook coordinate rejection, the Pi-facing bundle,
and the NUC-outage staleness drill.

`test_presence_resolver.py` covers pure precedence/expiry logic;
`test_presence_engine_db.py` covers the DB-backed orchestration directly.
This file is the HTTP surface admin UI / HA / the Pi actually talk to.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from control_plane_api.app import app
from control_plane_api.presence import is_stale
from control_plane_api.settings import override_settings
from fastapi.testclient import TestClient

from .conftest import TestSettings as _BaseTestSettings

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


def _issue_config_token(client: TestClient) -> str:
    resp = client.post(
        "/admin/tokens", json={"scope": "config", "door_id": "primary"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


# ---------------------------------------------------------------------------
# Auth boundaries
# ---------------------------------------------------------------------------


def test_admin_endpoints_require_admin_token(client: TestClient) -> None:
    assert client.get("/status/presence").status_code == 401
    assert client.get("/status/presence/owner").status_code == 401
    assert client.get("/status/presence/owner/history").status_code == 401
    assert (
        client.patch("/status/presence/owner", json={"tracking_enabled": False}).status_code == 401
    )
    assert client.post("/status/presence/owner/override", json={"label": "busy"}).status_code == 401
    assert client.delete("/status/presence/owner/override").status_code == 401
    assert (
        client.patch("/status/presence/owner/sources/manual", json={"enabled": False}).status_code
        == 401
    )
    assert (
        client.post(
            "/status/presence/webhook/focus-shortcut", json={"subject_id": "owner", "label": "busy"}
        ).status_code
        == 401
    )


def test_bundle_requires_config_scope_not_admin_or_ingest(client: TestClient) -> None:
    resp = client.post(
        "/admin/tokens", json={"scope": "ingest", "door_id": "primary"}, headers=ADMIN_HEADERS
    )
    ingest_token = resp.json()["token"]

    assert client.get("/status/presence/bundle").status_code == 401
    assert (
        client.get(
            "/status/presence/bundle", headers={"Authorization": f"Bearer {ingest_token}"}
        ).status_code
        == 401
    )

    config_token = _issue_config_token(client)
    ok = client.get("/status/presence/bundle", headers={"Authorization": f"Bearer {config_token}"})
    assert ok.status_code == 200, ok.text


# ---------------------------------------------------------------------------
# Default state, broad-label enum enforcement
# ---------------------------------------------------------------------------


def test_default_subjects_start_at_unknown_default(client: TestClient) -> None:
    resp = client.get("/status/presence", headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {s["subject_id"] for s in body["subjects"]}
    assert ids == {"owner", "roommate"}
    for subject in body["subjects"]:
        assert subject["label"] == "unknown"
        assert subject["source"] == "default"
        assert subject["tracking_enabled"] is True
        source_names = {s["source"] for s in subject["sources"]}
        assert source_names == {"manual", "focus_shortcut", "geofence_label", "calendar", "default"}


def test_override_rejects_a_label_outside_the_fixed_eight(client: TestClient) -> None:
    resp = client.post(
        "/status/presence/owner/override", json={"label": "sad"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 422


def test_webhook_rejects_a_label_outside_the_fixed_eight(client: TestClient) -> None:
    resp = client.post(
        "/status/presence/webhook/focus-shortcut",
        json={"subject_id": "owner", "label": "sad"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Precedence + manual override wins instantly; clearing hands control back
# ---------------------------------------------------------------------------


def test_manual_override_wins_over_webhook_sources_and_clearing_hands_control_back(
    client: TestClient,
) -> None:
    client.post(
        "/status/presence/webhook/geofence-label",
        json={"subject_id": "owner", "label": "at_library"},
        headers=ADMIN_HEADERS,
    )
    client.post(
        "/status/presence/webhook/focus-shortcut",
        json={"subject_id": "owner", "label": "sleeping"},
        headers=ADMIN_HEADERS,
    )
    before_override = client.get("/status/presence/owner", headers=ADMIN_HEADERS).json()
    assert before_override["label"] == "sleeping"
    assert before_override["source"] == "focus_shortcut"

    override_resp = client.post(
        "/status/presence/owner/override", json={"label": "busy"}, headers=ADMIN_HEADERS
    )
    assert override_resp.status_code == 200, override_resp.text
    assert override_resp.json()["label"] == "busy"
    assert override_resp.json()["source"] == "manual"

    clear_resp = client.delete("/status/presence/owner/override", headers=ADMIN_HEADERS)
    assert clear_resp.status_code == 200
    assert clear_resp.json()["label"] == "sleeping"
    assert clear_resp.json()["source"] == "focus_shortcut"


def test_expired_manual_override_falls_through_immediately(client: TestClient) -> None:
    client.post(
        "/status/presence/webhook/geofence-label",
        json={"subject_id": "owner", "label": "at_library"},
        headers=ADMIN_HEADERS,
    )
    already_expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    override_resp = client.post(
        "/status/presence/owner/override",
        json={"label": "busy", "until": already_expired},
        headers=ADMIN_HEADERS,
    )
    assert override_resp.status_code == 200, override_resp.text
    assert override_resp.json()["label"] == "at_library"
    assert override_resp.json()["source"] == "geofence_label"


# ---------------------------------------------------------------------------
# Per-source enable/disable, per-subject tracking_enabled
# ---------------------------------------------------------------------------


def test_disabling_a_source_removes_it_from_resolution_until_re_enabled(client: TestClient) -> None:
    client.post(
        "/status/presence/webhook/geofence-label",
        json={"subject_id": "owner", "label": "at_library"},
        headers=ADMIN_HEADERS,
    )
    assert client.get("/status/presence/owner", headers=ADMIN_HEADERS).json()["source"] == (
        "geofence_label"
    )

    disable_resp = client.patch(
        "/status/presence/owner/sources/geofence_label",
        json={"enabled": False},
        headers=ADMIN_HEADERS,
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["source"] == "default"
    disabled_source = next(
        s for s in disable_resp.json()["sources"] if s["source"] == "geofence_label"
    )
    assert disabled_source["enabled"] is False
    assert disabled_source["label"] == "at_library"  # value preserved, just not considered

    reenable_resp = client.patch(
        "/status/presence/owner/sources/geofence_label",
        json={"enabled": True},
        headers=ADMIN_HEADERS,
    )
    assert reenable_resp.json()["source"] == "geofence_label"


def test_disabling_an_unknown_source_is_a_validation_error(client: TestClient) -> None:
    resp = client.patch(
        "/status/presence/owner/sources/teleport", json={"enabled": False}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 422


def test_tracking_disabled_gates_inferred_sources_but_manual_still_works(
    client: TestClient,
) -> None:
    client.post(
        "/status/presence/webhook/geofence-label",
        json={"subject_id": "roommate", "label": "at_library"},
        headers=ADMIN_HEADERS,
    )
    patch_resp = client.patch(
        "/status/presence/roommate", json={"tracking_enabled": False}, headers=ADMIN_HEADERS
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["tracking_enabled"] is False
    assert patch_resp.json()["source"] == "default"  # geofence_label suppressed

    override_resp = client.post(
        "/status/presence/roommate/override", json={"label": "sleeping"}, headers=ADMIN_HEADERS
    )
    assert override_resp.json()["label"] == "sleeping"
    assert override_resp.json()["source"] == "manual"


# ---------------------------------------------------------------------------
# Webhook coordinate rejection (ARCHITECTURE.md §9: "no raw GPS anywhere")
# ---------------------------------------------------------------------------


def test_webhook_rejects_coordinate_bearing_payload_and_does_not_apply_it(
    client: TestClient,
) -> None:
    resp = client.post(
        "/status/presence/webhook/geofence-label",
        json={"subject_id": "owner", "label": "at_library", "latitude": 37.4, "longitude": -122.1},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422

    # Rejected payload must never have reached the source registry.
    status_after = client.get("/status/presence/owner", headers=ADMIN_HEADERS).json()
    assert status_after["source"] == "default"
    geofence = next(s for s in status_after["sources"] if s["source"] == "geofence_label")
    assert geofence["label"] is None


def test_webhook_rejects_nested_coordinate_field(client: TestClient) -> None:
    resp = client.post(
        "/status/presence/webhook/focus-shortcut",
        json={"subject_id": "owner", "label": "busy", "meta": {"gps": {"lat": 1, "lon": 2}}},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422


def test_webhook_sets_the_correct_source_not_client_supplied(client: TestClient) -> None:
    """The webhook route pins `source` server-side — a payload can't claim to be "manual"."""
    resp = client.post(
        "/status/presence/webhook/focus-shortcut",
        json={"subject_id": "owner", "label": "busy", "source": "manual"},
        headers=ADMIN_HEADERS,
    )
    # extra="forbid" on the request model rejects the unexpected "source" field outright.
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# History (label + source + timestamp only, newest first)
# ---------------------------------------------------------------------------


def test_history_records_label_source_timestamp_only_newest_first(client: TestClient) -> None:
    client.get("/status/presence/owner", headers=ADMIN_HEADERS)  # baseline -> unknown/default
    client.post("/status/presence/owner/override", json={"label": "busy"}, headers=ADMIN_HEADERS)
    client.delete("/status/presence/owner/override", headers=ADMIN_HEADERS)

    resp = client.get("/status/presence/owner/history", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    history = resp.json()["history"]
    assert [h["label"] for h in history] == ["unknown", "busy", "unknown"]
    assert [h["source"] for h in history] == ["default", "manual", "default"]
    for entry in history:
        assert set(entry.keys()) == {"label", "source", "until", "occurred_at"}
    # newest first
    timestamps = [entry["occurred_at"] for entry in history]
    assert timestamps == sorted(timestamps, reverse=True)


# ---------------------------------------------------------------------------
# Pi-facing bundle + NUC-outage staleness drill
# ---------------------------------------------------------------------------


def test_bundle_reflects_current_resolution_and_carries_staleness_fields(
    client: TestClient,
) -> None:
    client.post(
        "/status/presence/owner/override", json={"label": "do_not_disturb"}, headers=ADMIN_HEADERS
    )
    config_token = _issue_config_token(client)
    resp = client.get(
        "/status/presence/bundle", headers={"Authorization": f"Bearer {config_token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["door_id"] == "primary"
    assert "generated_at" in body
    assert body["stale_after_s"] > 0
    assert body["subjects"]["owner"]["label"] == "do_not_disturb"
    assert body["subjects"]["owner"]["source"] == "manual"
    assert "roommate" in body["subjects"]


def test_nuc_outage_drill_wallboard_shows_last_known_label_with_staleness_hint(
    client: TestClient,
) -> None:
    """Simulator scenario: NUC goes down right after a fetch; the Pi never
    talks to it again for the rest of this test, but must still be able to
    tell — from the bundle it already cached — that the label it's showing
    is stale, per docs/ui/wallboard.md's `as_of` staleness hint.
    """
    client.post(
        "/status/presence/owner/override", json={"label": "at_class"}, headers=ADMIN_HEADERS
    )
    config_token = _issue_config_token(client)
    fetched = client.get(
        "/status/presence/bundle", headers={"Authorization": f"Bearer {config_token}"}
    ).json()

    cached_label = fetched["subjects"]["owner"]["label"]
    generated_at = datetime.fromisoformat(fetched["generated_at"])
    stale_after_s = fetched["stale_after_s"]

    # NUC is up, Pi just fetched: not stale yet.
    assert is_stale(generated_at, now=generated_at, max_age_s=stale_after_s) is False

    # NUC outage drags on well past the threshold — the Pi (never having
    # talked to the NUC again) must now flag its cached copy as stale,
    # while still being able to render `cached_label` as a last-known value.
    outage_now = generated_at + timedelta(seconds=stale_after_s + 1)
    assert is_stale(generated_at, now=outage_now, max_age_s=stale_after_s) is True
    assert cached_label == "at_class"  # last-known label is still shown, just flagged stale


def test_retention_cap_is_configurable_and_trims_history(engine) -> None:
    class SmallRetentionSettings(_BaseTestSettings):
        presence_history_max_rows: int = 2

    override_settings(SmallRetentionSettings())
    with TestClient(app) as small_client:
        for label in ("busy", "available", "sleeping", "away"):
            resp = small_client.post(
                "/status/presence/owner/override", json={"label": label}, headers=ADMIN_HEADERS
            )
            assert resp.status_code == 200, resp.text
        history = small_client.get("/status/presence/owner/history", headers=ADMIN_HEADERS).json()[
            "history"
        ]
    assert len(history) == 2
    assert [h["label"] for h in history] == ["away", "sleeping"]
