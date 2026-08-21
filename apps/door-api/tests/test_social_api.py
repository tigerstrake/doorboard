"""HTTP-level tests for the /guestbook, /polls, /checkins, /social, and
/admin/* routes — request validation, error envelopes, and the admin-token
placeholder gate.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state
from door_api.visitor_tokens import decode_visitor_token, encode_visitor_token


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


def _visitor_token(client: TestClient) -> str:
    if state.machine.snapshot().session_id is None:
        response = client.post("/doorpad/ring")
        assert response.status_code == 200
    response = client.get("/visitor-token")
    assert response.status_code == 200
    return response.json()["token"]


# ---------------------------------------------------------------------------
# Guestbook
# ---------------------------------------------------------------------------


def test_create_and_list_guestbook_requires_admin_approval(client: TestClient) -> None:
    token = _visitor_token(client)
    resp = client.post("/guestbook", json={"text": "hi there", "session_token": token})
    assert resp.status_code == 201

    # Not visible publicly until approved.
    assert client.get("/guestbook").json()["entries"] == []


def test_guestbook_validation_error_returns_error_envelope(client: TestClient) -> None:
    resp = client.post("/guestbook", json={"text": "   ", "session_token": _visitor_token(client)})
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
    token = _visitor_token(client)

    for i in range(2):
        resp = client.post("/guestbook", json={"text": f"entry {i}", "session_token": token})
        assert resp.status_code == 201

    resp = client.post("/guestbook", json={"text": "one too many", "session_token": token})
    assert resp.status_code == 429
    assert resp.json()["detail"]["error"]["code"] == "rate_limited"


def test_trace_id_propagated_from_header(client: TestClient) -> None:
    resp = client.post(
        "/guestbook",
        json={"text": "   ", "session_token": _visitor_token(client)},
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
        json={"option_id": option_id, "session_token": _visitor_token(client)},
    )
    assert vote_resp.status_code == 201

    dup_resp = client.post(
        f"/polls/{poll['id']}/vote",
        json={"option_id": option_id, "session_token": _visitor_token(client)},
    )
    assert dup_resp.status_code == 409
    assert dup_resp.json()["detail"]["error"]["code"] == "already_voted"

    results = client.get(f"/polls/{poll['id']}/results").json()["results"]
    assert next(r for r in results if r["option_id"] == option_id)["votes"] == 1


# ---------------------------------------------------------------------------
# Check-ins
# ---------------------------------------------------------------------------


def test_checkin_create_and_stats(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate a real, server-side cached identity — the only legitimate
    # source of check-in attribution (vision.identity_stable equivalent).
    # consent_version matters now: attribution is gated on it (ADR-0018 §2).
    state.machine.handle_identity_stable(
        person_id="prs_alex",
        display_name="Alex",
        profile_id="blue_wave",
        consent_version="v3",
    )

    resp = client.post("/checkins", json={"label": "Alex", "session_token": _visitor_token(client)})
    assert resp.status_code == 201
    assert resp.json()["person_id"] == "prs_alex"

    # The frequency ranking is off by default at the owner's request, so this
    # test opts in explicitly to exercise the stat itself.
    monkeypatch.setenv("SOCIAL_FREQUENCY_STAT_ENABLED", "true")
    stats = client.get("/checkins/stats/most-frequent").json()["stat"]
    assert stats["person_id"] == "prs_alex"
    assert stats["count"] == 1


def test_recognised_person_can_check_in_with_no_active_session(client: TestClient) -> None:
    """The reported bug, end to end: "I tapped check in as Tiger and nothing happened".

    ADR-0020 lets the recognised name outlive the approach session, so "Check in as
    <name>" is reachable while the session is IDLE. Both halves of the token path have to
    accept that state — minting *and* verification. Fixing only the mint turned a 422 into
    a 401 and the button still did nothing, so this drives the whole HTTP path rather than
    the two endpoints separately.
    """
    state.machine.handle_identity_stable(
        person_id="prs_alex",
        display_name="Alex",
        profile_id="blue_wave",
        consent_version="v3",
    )
    # The face leaves the frame — someone standing at the doorpad looking down at it. The
    # approach session drops to IDLE; the held name deliberately survives (ADR-0020). This
    # is the state the doorpad was in when the button did nothing.
    state.machine.handle_identity_expired(person_id="prs_alex")
    assert state.machine.snapshot().session_id is None
    assert state.identity.current() is not None, "the name must outlive the session"

    minted = client.get("/visitor-token")
    assert minted.status_code == 200, "a held identity must be enough to mint a token"
    token = minted.json()["token"]
    assert token

    resp = client.post("/checkins", json={"label": "Alex", "session_token": token})
    assert resp.status_code == 201, resp.text
    assert resp.json()["person_id"] == "prs_alex", "the check-in must be attributed"


def test_visitor_token_is_refused_once_the_identity_is_forgotten(client: TestClient) -> None:
    # The flip side: widening verification must not make tokens immortal. Once the door
    # has forgotten the person, their token is worth nothing.
    state.machine.handle_identity_stable(
        person_id="prs_alex",
        display_name="Alex",
        profile_id="blue_wave",
        consent_version="v3",
    )
    state.machine.handle_identity_expired(person_id="prs_alex")
    token = client.get("/visitor-token").json()["token"]  # keyed on the interaction
    state.identity.forget()

    resp = client.post("/checkins", json={"label": "Alex", "session_token": token})
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"]["code"] == "inactive_visitor_session"


def test_checkin_ignores_client_supplied_person_id(client: TestClient) -> None:
    # No identity cached server-side — a client-claimed person_id must be
    # ignored, not trusted. Otherwise any visitor could attribute a
    # check-in to any known person_id.
    resp = client.post(
        "/checkins",
        json={
            "person_id": "prs_taylor",
            "label": "Taylor",
            "session_token": _visitor_token(client),
        },
    )
    assert resp.status_code == 201
    assert resp.json()["person_id"] is None

    # An anonymous check-in never counts toward the most-frequent-visitor stat.
    stats = client.get("/checkins/stats/most-frequent").json()["stat"]
    assert stats is None


def test_checkin_accepts_and_returns_photo_recording_id(client: TestClient) -> None:
    # A check-in may carry an optional reference to a visitor-captured photo
    # (ADR-0013). The reference round-trips through create and list.
    resp = client.post(
        "/checkins",
        json={
            "label": "Alex",
            "photo_recording_id": "rec_photo_abc",
            "session_token": _visitor_token(client),
        },
    )
    assert resp.status_code == 201
    assert resp.json()["photo_recording_id"] == "rec_photo_abc"

    listed = client.get("/checkins").json()["checkins"]
    assert listed[0]["photo_recording_id"] == "rec_photo_abc"


def test_checkin_without_photo_returns_null_reference(client: TestClient) -> None:
    resp = client.post(
        "/checkins",
        json={"label": "Alex", "session_token": _visitor_token(client)},
    )
    assert resp.status_code == 201
    assert resp.json()["photo_recording_id"] is None


# ---------------------------------------------------------------------------
# Deletion requests
# ---------------------------------------------------------------------------


def test_deletion_request_unsupported_target_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/social/deletion-requests",
        json={
            "target_kind": "video_message",
            "target_id": "abc",
            "session_token": _visitor_token(client),
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"]["code"] == "unsupported_target"


def test_deletion_request_removes_guestbook_entry(client: TestClient) -> None:
    token = _visitor_token(client)
    create_resp = client.post("/guestbook", json={"text": "hi there", "session_token": token})
    entry_id = create_resp.json()["id"]

    del_resp = client.post(
        "/social/deletion-requests",
        json={"target_kind": "guestbook", "target_id": entry_id, "session_token": token},
    )
    assert del_resp.status_code == 202


def test_public_write_rejects_tampered_and_expired_tokens(client: TestClient) -> None:
    token = _visitor_token(client)
    tampered = f"{token[:-1]}{'A' if token[-1] != 'A' else 'B'}"
    bad_signature = client.post("/guestbook", json={"text": "hi", "session_token": tampered})
    assert bad_signature.status_code == 401

    session_id = state.machine.snapshot().session_id
    assert session_id is not None
    expired = encode_visitor_token(
        secret=state.config.visitor_token_secret,
        session_id=session_id,
        expires_at=1,
    )
    expired_response = client.post("/guestbook", json={"text": "hi", "session_token": expired})
    assert expired_response.status_code == 401


def test_visitor_token_rejects_noncanonical_signature_alias(client: TestClient) -> None:
    token = _visitor_token(client)
    payload, signature = token.split(".", maxsplit=1)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last_index = alphabet.index(signature[-1])
    assert last_index % 4 == 0
    alias = f"{payload}.{signature[:-1]}{alphabet[last_index + 1]}"

    with pytest.raises(ValueError, match="malformed visitor token"):
        decode_visitor_token(alias, secret=state.config.visitor_token_secret)


def test_visitor_can_only_delete_content_from_same_session(client: TestClient) -> None:
    first_token = _visitor_token(client)
    created = client.post(
        "/guestbook", json={"text": "first session", "session_token": first_token}
    )
    entry_id = created.json()["id"]

    state.machine.handle_admin_reset()
    state.machine.handle_admin_reset()
    second_token = _visitor_token(client)
    response = client.post(
        "/social/deletion-requests",
        json={"target_kind": "guestbook", "target_id": entry_id, "session_token": second_token},
    )
    assert response.status_code == 404


def test_visitor_session_endpoint_rejects_inactive_session(client: TestClient) -> None:
    token = _visitor_token(client)
    assert client.get("/visitor-session", params={"token": token}).status_code == 200
    state.machine.handle_admin_reset()
    state.machine.handle_admin_reset()
    assert client.get("/visitor-session", params={"token": token}).status_code == 401


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

    create_resp = client.post(
        "/guestbook", json={"text": "hi there", "session_token": _visitor_token(client)}
    )
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
    create_resp = client.post(
        "/guestbook", json={"text": hostile, "session_token": _visitor_token(client)}
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]
    assert create_resp.json()["text"] == hostile  # stored raw, not double-escaped

    client.post(f"/admin/guestbook/{entry_id}/approve", headers=auth)
    listed = client.get("/guestbook").json()["entries"]
    assert listed[0]["text"] == hostile


# ---------------------------------------------------------------------------
# Attribution (ADR-0018 P-29, P-31, P-32)
# ---------------------------------------------------------------------------


def _recognise(consent_version: str | None) -> None:
    """Put a recognised person in the session, as vision.identity_stable would."""
    state.machine.handle_identity_stable(
        person_id="prs_tiger",
        display_name="Tiger",
        profile_id="warm_amber",
        consent_version=consent_version,
    )


def test_attribution_binds_the_recognised_person(client: TestClient) -> None:
    """P-29: a write during a recognised session carries that person."""
    _recognise("v3")
    token = _visitor_token(client)

    entry = client.post("/guestbook", json={"text": "Left a note", "session_token": token}).json()
    stored = state.social_service.store.get_guestbook_entry(entry["id"])
    assert stored is not None
    assert stored.person_id == "prs_tiger"


def test_an_unrecognised_visitor_is_not_attributed(client: TestClient) -> None:
    """P-29 / E-25: recognition adds attribution; its absence changes nothing."""
    token = _visitor_token(client)
    entry = client.post(
        "/guestbook", json={"text": "Anonymous note", "session_token": token}
    ).json()

    stored = state.social_service.store.get_guestbook_entry(entry["id"])
    assert stored is not None
    assert stored.person_id is None
    # And the write itself succeeded — attribution is never a gate on writing.
    assert entry["text"] == "Anonymous note"


@pytest.mark.parametrize("version", ["v1", "v2", None, "", "banana"])
def test_older_consent_is_not_attributed(client: TestClient, version: str | None) -> None:
    """Someone enrolled before v3 agreed to a greeting, not to attribution.

    Fails closed on unparseable versions rather than opting them in.
    """
    _recognise(version)
    token = _visitor_token(client)

    entry = client.post(
        "/guestbook", json={"text": f"note under {version}", "session_token": token}
    ).json()
    stored = state.social_service.store.get_guestbook_entry(entry["id"])
    assert stored is not None
    assert stored.person_id is None, f"consent {version!r} must not be attributed"


def test_votes_are_attributed_to_the_recognised_voter(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attributed votes are not secret ballots — the owner's informed choice."""
    monkeypatch.setenv("DOOR_API_SOCIAL_ADMIN_TOKEN", "test-admin-token")
    # Rebuild so the frozen SocialConfig picks the token up, as the existing
    # admin-route tests do.
    state.__init__()
    state.startup()
    poll = client.post(
        "/admin/polls",
        json={"question": "Best sound?", "options": ["Chime", "Buzz"]},
        headers={"Authorization": "Bearer test-admin-token"},
    ).json()
    _recognise("v3")

    resp = client.post(
        f"/polls/{poll['id']}/vote",
        json={"option_id": poll["options"][0]["id"], "session_token": _visitor_token(client)},
    )
    assert resp.status_code == 201

    rows = state.social_service.store._conn.execute(
        "SELECT person_id FROM poll_votes WHERE poll_id = ?", (poll["id"],)
    ).fetchall()
    assert [r[0] for r in rows] == ["prs_tiger"]


def test_client_supplied_identity_is_never_trusted_for_a_note(client: TestClient) -> None:
    """Attribution comes from the door's own recognition, never from the body."""
    token = _visitor_token(client)
    entry = client.post(
        "/guestbook",
        json={"text": "not mine", "person_id": "prs_someone_else", "session_token": token},
    ).json()
    stored = state.social_service.store.get_guestbook_entry(entry["id"])
    assert stored is not None
    assert stored.person_id is None


def test_public_guestbook_never_exposes_the_internal_person_id(client: TestClient) -> None:
    """P-31: person_id is an internal opaque id and has no place on a public route."""
    _recognise("v3")
    token = _visitor_token(client)
    created = client.post("/guestbook", json={"text": "public note", "session_token": token}).json()
    assert "person_id" not in created

    listed = client.get("/guestbook").json()
    assert "person_id" not in json.dumps(listed)


def test_public_routes_never_expose_arrival_history(client: TestClient) -> None:
    """P-31: the visit log is door-visiond's, admin-only, and has no door-api route."""
    for path in ("/guestbook", "/polls/current", "/checkins", "/checkins/stats/most-frequent"):
        body = json.dumps(client.get(path).json())
        for leaked in ("arrived_at", "last_seen_at", "visit_id", "visits"):
            assert leaked not in body, f"{path} exposed {leaked}"


def test_frequency_ranking_is_off_by_default(client: TestClient) -> None:
    """P-32: "who was here most" stays absent unless explicitly enabled."""
    state.machine.handle_identity_stable(
        person_id="prs_tiger", display_name="Tiger", profile_id="warm_amber", consent_version="v3"
    )
    client.post("/checkins", json={"label": "Tiger", "session_token": _visitor_token(client)})

    assert client.get("/checkins/stats/most-frequent").json()["stat"] is None


def test_guestbook_photo_reference_is_owner_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0033: a note may carry a photo reference, but only the owner sees it.

    The public guestbook renders on a wallboard facing a shared hallway and a
    photo stays private until the owner approves it, so exposing the reference
    publicly would reveal which entries carry a face before that decision.
    """
    monkeypatch.setenv("DOOR_API_SOCIAL_ADMIN_TOKEN", "correct-token")
    state.__init__()
    state.startup()
    auth = {"Authorization": "Bearer correct-token"}

    photo_id = "b7e4c318-2a51-4f0d-9c22-6d3f1e8a4402"
    create_resp = client.post(
        "/guestbook",
        json={
            "text": "left a picture too",
            "session_token": _visitor_token(client),
            "photo_recording_id": photo_id,
        },
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["id"]
    # Absent from the response the visitor gets back.
    assert "photo_recording_id" not in create_resp.json()

    # Present for the owner.
    pending = client.get("/admin/guestbook?status=pending", headers=auth).json()["entries"]
    entry = next(e for e in pending if e["id"] == entry_id)
    assert entry["photo_recording_id"] == photo_id

    # Still absent once public.
    assert client.post(f"/admin/guestbook/{entry_id}/approve", headers=auth).status_code == 200
    public = client.get("/guestbook").json()["entries"]
    assert public[0]["id"] == entry_id
    assert "photo_recording_id" not in public[0]


def test_guestbook_without_a_photo_still_works(client: TestClient) -> None:
    """The photo is optional on a note — unlike a check-in, which always offers one."""
    resp = client.post(
        "/guestbook", json={"text": "just a note", "session_token": _visitor_token(client)}
    )
    assert resp.status_code == 201
