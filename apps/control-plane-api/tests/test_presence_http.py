"""Presence engine over HTTP (T-504): auth boundaries, request validation,
manual-override-wins, webhook coordinate rejection, the Pi-facing bundle,
and the NUC-outage staleness drill.

`test_presence_resolver.py` covers pure precedence/expiry logic;
`test_presence_engine_db.py` covers the DB-backed orchestration directly.
This file is the HTTP surface admin UI / HA / the Pi actually talk to.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from control_plane_api import state as state_module
from control_plane_api.app import app
from control_plane_api.models import PresenceHistoryRow, PresenceSourceRow, PresenceSubjectRow
from control_plane_api.mqtt import PRESENCE_TOPIC, RecordingMqttPublisher, presence_topic_for
from control_plane_api.presence import is_stale
from control_plane_api.settings import override_settings
from fastapi.testclient import TestClient
from sqlalchemy import select

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
        assert source_names == {
            "manual",
            "focus_shortcut",
            "geofence_label",
            "calendar",
            "schedule",
            "default",
        }


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


# ---------------------------------------------------------------------------
# A webhook label that arrives with no expiry must not live forever
# ---------------------------------------------------------------------------


def test_a_webhook_label_without_an_expiry_still_gets_one(client: TestClient) -> None:
    """The phone is the only thing that can clear a Focus, and phones die.

    `until` comes from the caller, so a label sent without one persisted until an
    explicit `label: null` arrived. A phone that runs out of battery mid-Focus, or
    a Shortcut that fails silently, pinned the door to that label indefinitely.
    """
    resp = client.post(
        "/status/presence/webhook/focus-shortcut",
        json={"subject_id": "owner", "label": "busy"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["until"] is not None, "an unbounded Focus never falls through"


def test_an_explicit_expiry_from_the_caller_is_honoured(client: TestClient) -> None:
    """The safety net is a floor for the careless case, not an override."""
    explicit = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    resp = client.post(
        "/status/presence/webhook/focus-shortcut",
        json={"subject_id": "owner", "label": "busy", "until": explicit},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    returned = datetime.fromisoformat(resp.json()["until"])
    assert abs((returned - datetime.fromisoformat(explicit)).total_seconds()) < 1


def test_clearing_a_source_does_not_invent_an_expiry(client: TestClient) -> None:
    """`label: null` means gone now, not gone in 18 hours."""
    client.post(
        "/status/presence/webhook/focus-shortcut",
        json={"subject_id": "owner", "label": "busy"},
        headers=ADMIN_HEADERS,
    )
    resp = client.post(
        "/status/presence/webhook/focus-shortcut",
        json={"subject_id": "owner", "label": None},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] != "focus_shortcut", "clearing must hand control back at once"


def test_a_stale_focus_stops_suppressing_the_nightly_window(client: TestClient) -> None:
    """The bug this is really about (ADR-0037).

    focus_shortcut outranks `schedule`, so a Focus label that never expires does
    not merely go stale — it permanently cancels the nightly Recovery window. The
    owner's stated design is "Recovery every night unless another Focus is on, and
    when that Focus turns off Recovery comes back"; one missed clear turned that
    into "Recovery never again" with nothing logged.
    """

    class ShortLivedWebhookSettings(_BaseTestSettings):
        presence_webhook_max_age_s: float = 1.0
        presence_schedule_window: str = "00:00-23:59"
        presence_schedule_label: str = "sleeping"
        presence_schedule_subjects: str = "owner"

    override_settings(ShortLivedWebhookSettings())
    with TestClient(app) as scoped:
        scoped.post(
            "/status/presence/webhook/focus-shortcut",
            json={"subject_id": "owner", "label": "busy"},
            headers=ADMIN_HEADERS,
        )
        held = scoped.get("/status/presence/owner", headers=ADMIN_HEADERS).json()
        assert held["source"] == "focus_shortcut", "an active Focus should win while it lasts"

        time.sleep(1.1)

        after = scoped.get("/status/presence/owner", headers=ADMIN_HEADERS).json()
        assert after["source"] == "schedule", (
            "once the Focus expires the nightly window must come back, "
            f"got source={after['source']!r} label={after['label']!r}"
        )


def test_normalising_a_stored_expiry_to_utc(client: TestClient) -> None:
    """Postgres renders `timestamptz` in the *session's* timezone, not UTC.

    The instant is right, the tzinfo is not, and
    `StatusPresenceChangedPayload.until` requires UTC — so resolving any source
    that carried an expiry raised ValidationError and the request 500'd. It needed
    both a stored `until` and a non-UTC server, so a UTC-configured CI never saw
    it; the machine this was found on runs Postgres in Europe/Rome.

    Asserted on the helper directly with an explicitly non-UTC input, so it keeps
    testing something no matter how the runner's database is configured — an
    end-to-end version would agree with itself on a UTC server and prove nothing.
    """
    from zoneinfo import ZoneInfo

    from control_plane_api.presence_engine import _as_utc

    instant = datetime(2026, 9, 10, 22, 0, tzinfo=ZoneInfo("Europe/Rome"))
    normalised = _as_utc(instant)

    assert normalised is not None
    assert normalised.tzinfo is UTC, "the contract rejects any tzinfo that is not UTC"
    assert normalised == instant, "same instant — this is a relabel, not a shift"
    assert _as_utc(None) is None
    # A naive value is assumed UTC rather than reinterpreted as local time, which
    # would silently move the expiry by the server's offset.
    naive = datetime(2026, 9, 10, 22, 0)
    assert _as_utc(naive) == datetime(2026, 9, 10, 22, 0, tzinfo=UTC)


def test_an_override_with_an_expiry_resolves_instead_of_500ing(client: TestClient) -> None:
    """The end-to-end path that was broken: a stored expiry must survive resolution."""
    resp = client.post(
        "/status/presence/owner/override",
        json={"label": "busy", "until": (datetime.now(UTC) + timedelta(hours=2)).isoformat()},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text

    status = client.get("/status/presence/owner", headers=ADMIN_HEADERS)
    assert status.status_code == 200, status.text
    assert status.json()["source"] == "manual"


# ---------------------------------------------------------------------------
# Subjects are an allow-list (seeded defaults + admin-created), never client-invented
# ---------------------------------------------------------------------------


@pytest.fixture
def recording_client(engine, monkeypatch: pytest.MonkeyPatch):
    """A TestClient whose AppState publishes to a recording MQTT double."""
    recorder = RecordingMqttPublisher()
    monkeypatch.setattr(state_module, "build_publisher", lambda **kwargs: recorder)
    with TestClient(app) as c:
        yield c, recorder


def _subject_rows(session_factory, subject_id: str) -> tuple[int, int, int]:
    with session_factory() as session:
        subjects = session.execute(
            select(PresenceSubjectRow).where(PresenceSubjectRow.subject_id == subject_id)
        ).all()
        sources = session.execute(
            select(PresenceSourceRow).where(PresenceSourceRow.subject_id == subject_id)
        ).all()
        history = session.execute(
            select(PresenceHistoryRow).where(PresenceHistoryRow.subject_id == subject_id)
        ).all()
    return len(subjects), len(sources), len(history)


def test_a_webhook_for_an_unknown_subject_is_404_and_writes_and_publishes_nothing(
    recording_client, session_factory
) -> None:
    """Before: any string on the LAN-reachable webhook became a permanent, published,
    retained, wallboard-rendered subject with no way to delete it."""
    client, recorder = recording_client
    resp = client.post(
        "/status/presence/webhook/focus-shortcut",
        json={"subject_id": "stranger", "label": "busy"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 404, resp.text
    assert _subject_rows(session_factory, "stranger") == (0, 0, 0)
    assert not [e for _t, e in recorder.published if "stranger" in e.model_dump_json()]
    listed = {
        s["subject_id"]
        for s in client.get("/status/presence", headers=ADMIN_HEADERS).json()["subjects"]
    }
    assert listed == {"owner", "roommate"}


@pytest.mark.parametrize(
    "subject_id",
    ["x" * 33, "x" * 65, "Owner", "a b", "../owner", "owner;drop", ""],
)
def test_malformed_subject_ids_are_422_on_the_webhook_and_in_the_path(
    client: TestClient, subject_id: str
) -> None:
    resp = client.post(
        "/status/presence/webhook/geofence-label",
        json={"subject_id": subject_id, "label": "away"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422, resp.text
    if subject_id and "/" not in subject_id:
        # In the path too: a 65-char id used to reach Postgres and 500 on DataError.
        assert client.get(f"/status/presence/{subject_id}", headers=ADMIN_HEADERS).status_code in (
            404,
            422,
        )
        assert (
            client.post(
                f"/status/presence/{subject_id}/override",
                json={"label": "busy"},
                headers=ADMIN_HEADERS,
            ).status_code
            == 422
        )


def test_admin_get_and_override_of_an_unknown_subject_are_404(client: TestClient) -> None:
    assert client.get("/status/presence/guest", headers=ADMIN_HEADERS).status_code == 404
    assert client.get("/status/presence/guest/history", headers=ADMIN_HEADERS).status_code == 404
    assert (
        client.post(
            "/status/presence/guest/override", json={"label": "busy"}, headers=ADMIN_HEADERS
        ).status_code
        == 404
    )
    assert (
        client.delete("/status/presence/guest/override", headers=ADMIN_HEADERS).status_code == 404
    )
    assert (
        client.patch(
            "/status/presence/guest/sources/manual", json={"enabled": False}, headers=ADMIN_HEADERS
        ).status_code
        == 404
    )


def test_admin_patch_creates_a_subject_the_webhook_then_accepts(recording_client) -> None:
    client, recorder = recording_client
    created = client.patch(
        "/status/presence/guest", json={"tracking_enabled": True}, headers=ADMIN_HEADERS
    )
    assert created.status_code == 200, created.text
    resp = client.post(
        "/status/presence/webhook/focus-shortcut",
        json={"subject_id": "guest", "label": "busy"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["label"] == "busy"
    assert presence_topic_for("guest") in recorder.topics


def test_admin_delete_removes_the_subject_its_rows_and_the_retained_topics(
    recording_client, session_factory
) -> None:
    client, recorder = recording_client
    client.patch("/status/presence/guest", json={"tracking_enabled": True}, headers=ADMIN_HEADERS)
    client.post(
        "/status/presence/webhook/focus-shortcut",
        json={"subject_id": "guest", "label": "busy"},
        headers=ADMIN_HEADERS,
    )
    # One source row; two history rows (unknown/default on creation, then busy).
    assert _subject_rows(session_factory, "guest") == (1, 1, 2)

    resp = client.delete("/status/presence/guest", headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "subject_id": "guest",
        "status": "deleted",
        "sources_deleted": 1,
        "history_deleted": 2,
    }
    assert _subject_rows(session_factory, "guest") == (0, 0, 0)
    # Both retained topic forms are forgotten on the broker (ADR-0048 transition).
    assert recorder.cleared == [presence_topic_for("guest"), PRESENCE_TOPIC]
    assert client.get("/status/presence/guest", headers=ADMIN_HEADERS).status_code == 404
    assert client.delete("/status/presence/guest", headers=ADMIN_HEADERS).status_code == 404
    listed = {
        s["subject_id"]
        for s in client.get("/status/presence", headers=ADMIN_HEADERS).json()["subjects"]
    }
    assert listed == {"owner", "roommate"}


def test_default_subjects_cannot_be_deleted(client: TestClient) -> None:
    assert client.delete("/status/presence/owner", headers=ADMIN_HEADERS).status_code == 409
    assert client.delete("/status/presence/roommate", headers=ADMIN_HEADERS).status_code == 409
    assert client.get("/status/presence/owner", headers=ADMIN_HEADERS).status_code == 200


def test_subject_delete_requires_admin(client: TestClient) -> None:
    assert client.delete("/status/presence/guest").status_code == 401
