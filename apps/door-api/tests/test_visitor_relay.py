"""Visitor relay on the door-api side (ADR-0017 P-20, P-23, P-24, P-25).

The relay exists so the wallboard QR works for a phone on cellular. These tests
cover the three things that could go wrong in a way a user would feel: the
snapshot leaking something private, a duplicate delivery double-posting, and the
QR pointing at a dead relay when the LAN would have worked.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from typing import Any

import pytest
from doorboard_contracts.enrollment_relay import (
    VISITOR_SNAPSHOT_FIELDS,
    VisitorActionOutcome,
    VisitorNoteAction,
    VisitorQueuedAction,
    VisitorSessionSnapshot,
    VisitorVoteAction,
)
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state  # noqa: E402
from door_api.visitor_relay import (  # noqa: E402
    VisitorRelayError,
    VisitorRelayWorker,
    new_action_id,
    opaque_session_id,
    sha256_b64url,
)

RELAY_BASE = "https://relay.example.test"
NOTE_SENTINEL = "DOORBOARD_SENTINEL_VISITOR_NOTE"


class FakeVisitorTransport:
    """Records pushes and hands back whatever actions a test queued."""

    def __init__(self) -> None:
        self.pushed: list[VisitorSessionSnapshot] = []
        self.acked: list[dict[str, Any]] = []
        self.queued: list[VisitorQueuedAction] = []
        self.polls = 0
        self.fail_with: Exception | None = None

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def push_snapshot(self, snapshot: VisitorSessionSnapshot) -> None:
        self._maybe_fail()
        self.pushed.append(snapshot)

    def poll_actions(self) -> list[VisitorQueuedAction]:
        self.polls += 1
        self._maybe_fail()
        actions = list(self.queued)
        self.queued.clear()
        return actions

    def acknowledge(self, outcomes: list[dict[str, Any]]) -> None:
        self._maybe_fail()
        self.acked.extend(outcomes)


@pytest.fixture
def transport() -> FakeVisitorTransport:
    return FakeVisitorTransport()


@pytest.fixture
def relay_state(
    transport: FakeVisitorTransport, monkeypatch: pytest.MonkeyPatch
) -> Generator[Any, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_VISITOR_RELAY_BASE_URL", RELAY_BASE)
    monkeypatch.setenv("DOOR_API_VISITOR_RELAY_DEVICE_TOKEN", "visitor-device-token")
    state.__init__(visitor_relay_transport=transport)
    state.startup()
    yield state
    state.shutdown()


def _ring(client: TestClient) -> None:
    """Drive the machine into a live visitor session."""
    resp = client.post("/doorpad/ring")
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] is True


def _issue_token(relay_state: Any, client: TestClient) -> str:
    _ring(client)
    body = client.get("/visitor-token").json()
    assert "token" in body, body
    return str(body["token"])


# -- P-20: the snapshot carries nothing private ----------------------------


def test_snapshot_fields_match_the_binding_allow_list(relay_state: Any) -> None:
    client = TestClient(app)
    _issue_token(relay_state, client)
    snapshot = relay_state.visitor_relay_snapshot()
    assert snapshot is not None
    assert set(snapshot.model_dump().keys()) == VISITOR_SNAPSHOT_FIELDS


def test_snapshot_carries_no_identity_media_or_diagnostics(relay_state: Any) -> None:
    """P-20: byte-scan the serialised snapshot rather than trusting the shape."""
    client = TestClient(app)
    _issue_token(relay_state, client)

    snapshot = relay_state.visitor_relay_snapshot()
    assert snapshot is not None
    serialised = snapshot.model_dump_json()

    for forbidden in (
        "display_name",
        "person_id",
        "profile_id",
        "embedding",
        "recording_id",
        "thumbnail",
        "free_bytes",
        "queue_depth",
        "admin",
        "db_path",
    ):
        assert forbidden not in serialised, f"{forbidden} reached the public snapshot"


def test_snapshot_never_contains_the_raw_session_token(relay_state: Any) -> None:
    client = TestClient(app)
    token = _issue_token(relay_state, client)
    snapshot = relay_state.visitor_relay_snapshot()
    assert snapshot is not None
    serialised = snapshot.model_dump_json()
    assert token not in serialised
    assert snapshot.session_token_sha256 == sha256_b64url(token)


def test_snapshot_hides_the_internal_session_id(relay_state: Any) -> None:
    client = TestClient(app)
    _issue_token(relay_state, client)
    internal = str(relay_state.machine.snapshot().session_id)
    snapshot = relay_state.visitor_relay_snapshot()
    assert snapshot is not None
    assert internal not in snapshot.model_dump_json()
    assert snapshot.session_id == opaque_session_id(internal)
    assert snapshot.session_id.startswith("ses_")


def test_no_snapshot_without_a_live_session(relay_state: Any) -> None:
    assert relay_state.visitor_relay_snapshot() is None


# -- P-23: collected actions apply exactly once ----------------------------


def test_a_collected_note_becomes_a_guestbook_entry(relay_state: Any) -> None:
    client = TestClient(app)
    _issue_token(relay_state, client)
    snapshot = relay_state.visitor_relay_snapshot()
    assert snapshot is not None

    action = VisitorQueuedAction(
        action_id=new_action_id(),
        session_id=snapshot.session_id,
        submitted_at=snapshot.pushed_at,
        note=VisitorNoteAction(text=NOTE_SENTINEL),
    )
    outcome = relay_state.visitor_relay_apply(action)

    assert outcome.status == "applied", outcome.reason
    assert outcome.entry_id
    entries = relay_state.social_service.list_admin_guestbook_entries(
        status="pending", limit=10, cursor=None
    )
    assert any(NOTE_SENTINEL in entry.text for entry in entries)


def test_duplicate_delivery_applies_once(relay_state: Any) -> None:
    """P-23: the relay leases rather than deletes, so this is a real case."""
    client = TestClient(app)
    _issue_token(relay_state, client)
    snapshot = relay_state.visitor_relay_snapshot()
    assert snapshot is not None

    action = VisitorQueuedAction(
        action_id=new_action_id(),
        session_id=snapshot.session_id,
        submitted_at=snapshot.pushed_at,
        note=VisitorNoteAction(text=NOTE_SENTINEL),
    )
    first = relay_state.visitor_relay_apply(action)
    second = relay_state.visitor_relay_apply(action)

    assert first.status == "applied"
    assert second == first, "a duplicate delivery must return the memoised outcome"
    entries = relay_state.social_service.list_admin_guestbook_entries(
        status="pending", limit=10, cursor=None
    )
    matching = [entry for entry in entries if NOTE_SENTINEL in entry.text]
    assert len(matching) == 1, f"note was posted {len(matching)} times"


def test_a_vote_for_a_missing_poll_is_rejected_not_raised(relay_state: Any) -> None:
    client = TestClient(app)
    _issue_token(relay_state, client)
    snapshot = relay_state.visitor_relay_snapshot()
    assert snapshot is not None

    outcome = relay_state.visitor_relay_apply(
        VisitorQueuedAction(
            action_id=new_action_id(),
            session_id=snapshot.session_id,
            submitted_at=snapshot.pushed_at,
            vote=VisitorVoteAction(poll_id="poll_missing", option_id="opt_x"),
        )
    )
    assert outcome.status == "rejected"
    assert outcome.reason == "not_found"


def test_an_empty_action_is_rejected(relay_state: Any) -> None:
    client = TestClient(app)
    _issue_token(relay_state, client)
    snapshot = relay_state.visitor_relay_snapshot()
    assert snapshot is not None

    outcome = relay_state.visitor_relay_apply(
        VisitorQueuedAction(
            action_id=new_action_id(),
            session_id=snapshot.session_id,
            submitted_at=snapshot.pushed_at,
        )
    )
    assert outcome.status == "rejected"
    assert outcome.reason == "empty_action"


def test_rejection_reasons_never_echo_visitor_text(relay_state: Any) -> None:
    """Reasons travel to a public page, so they must stay machine-readable."""
    client = TestClient(app)
    _issue_token(relay_state, client)
    snapshot = relay_state.visitor_relay_snapshot()
    assert snapshot is not None

    outcome = relay_state.visitor_relay_apply(
        VisitorQueuedAction(
            action_id=new_action_id(),
            session_id=snapshot.session_id,
            submitted_at=snapshot.pushed_at,
            vote=VisitorVoteAction(poll_id=NOTE_SENTINEL, option_id=NOTE_SENTINEL),
        )
    )
    assert outcome.status == "rejected"
    assert outcome.reason is not None
    assert NOTE_SENTINEL not in outcome.reason
    assert len(outcome.reason) <= 200


# -- P-24: the QR falls back to the LAN ------------------------------------


def test_qr_uses_the_lan_url_before_the_relay_is_observed(relay_state: Any) -> None:
    client = TestClient(app)
    _ring(client)
    body = client.get("/visitor-token").json()

    assert body["via"] == "lan"
    assert body["url"].startswith("http://door.local/visitor?token=")
    assert relay_state.visitor_relay_status()["qr_target"] == "lan"


@pytest.mark.anyio
async def test_qr_uses_the_relay_once_it_is_observed_reachable(
    relay_state: Any, transport: FakeVisitorTransport
) -> None:
    worker = VisitorRelayWorker(
        transport=transport, handler=relay_state, poll_interval_s=0.01, backoff_max_s=0.05
    )
    relay_state.visitor_relay_worker = worker
    await worker._tick()  # one successful exchange marks the relay fresh

    client = TestClient(app)
    _ring(client)
    body = client.get("/visitor-token").json()

    assert body["via"] == "relay"
    assert body["url"].startswith(f"{RELAY_BASE}/v/")
    assert relay_state.visitor_relay_status()["qr_target"] == "relay"


@pytest.mark.anyio
async def test_qr_reverts_to_lan_when_the_relay_goes_stale(
    relay_state: Any, transport: FakeVisitorTransport
) -> None:
    """E-19: staleness, not just failure, must push the QR back to the LAN."""
    worker = VisitorRelayWorker(
        transport=transport, handler=relay_state, poll_interval_s=0.01, backoff_max_s=0.05
    )
    relay_state.visitor_relay_worker = worker
    await worker._tick()

    # Pretend the last success was long enough ago to fall outside the window.
    worker.stats.last_success_monotonic = (
        asyncio.get_running_loop().time() - relay_state.config.visitor_relay_freshness_s - 1
    )

    client = TestClient(app)
    _ring(client)
    assert client.get("/visitor-token").json()["via"] == "lan"


def test_relay_status_reports_disabled_without_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.delenv("DOOR_API_VISITOR_RELAY_BASE_URL", raising=False)
    monkeypatch.delenv("DOOR_API_VISITOR_RELAY_DEVICE_TOKEN", raising=False)
    state.__init__()
    state.startup()
    try:
        assert state.visitor_relay_status() == {"configured": False, "status": "disabled"}
        assert state.visitor_relay_worker is None
    finally:
        state.shutdown()


# -- P-25: a broken relay never blocks the session -------------------------


@pytest.mark.anyio
async def test_relay_outage_never_blocks_the_session(
    relay_state: Any, transport: FakeVisitorTransport
) -> None:
    transport.fail_with = VisitorRelayError("relay unreachable: ConnectionError")
    worker = VisitorRelayWorker(
        transport=transport, handler=relay_state, poll_interval_s=0.01, backoff_max_s=0.05
    )
    relay_state.visitor_relay_worker = worker
    await worker.start()
    try:
        for _ in range(200):
            if worker.stats.consecutive_failures >= 3:
                break
            await asyncio.sleep(0.01)

        # The session flow still works, and the QR quietly falls back to the LAN.
        client = TestClient(app)
        _ring(client)
        body = client.get("/visitor-token").json()
        assert body["via"] == "lan"
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/session").status_code == 200
    finally:
        await worker.stop()

    assert worker.stats.consecutive_failures >= 3
    assert worker.stats.last_error == "VisitorRelayError"


def test_backoff_is_bounded(relay_state: Any, transport: FakeVisitorTransport) -> None:
    worker = VisitorRelayWorker(
        transport=transport, handler=relay_state, poll_interval_s=1.0, backoff_max_s=5.0
    )
    delays = [worker._register_failure(VisitorRelayError("boom")) for _ in range(20)]
    assert max(delays) <= 5.0
    assert delays[-1] == 5.0


def test_transport_refuses_plain_http_to_a_remote_host() -> None:
    from door_api.visitor_relay import HttpVisitorRelayTransport

    transport = HttpVisitorRelayTransport(
        base_url="http://relay.example.test", device_token="t", timeout_s=1.0
    )
    with pytest.raises(VisitorRelayError, match="https"):
        transport.poll_actions()


# -- outcome plumbing ------------------------------------------------------


@pytest.mark.anyio
async def test_worker_acks_with_the_session_id_the_relay_needs(
    relay_state: Any, transport: FakeVisitorTransport
) -> None:
    client = TestClient(app)
    _issue_token(relay_state, client)
    snapshot = relay_state.visitor_relay_snapshot()
    assert snapshot is not None

    transport.queued.append(
        VisitorQueuedAction(
            action_id=new_action_id(),
            session_id=snapshot.session_id,
            submitted_at=snapshot.pushed_at,
            note=VisitorNoteAction(text="hello from the doorstep"),
        )
    )
    worker = VisitorRelayWorker(
        transport=transport, handler=relay_state, poll_interval_s=0.01, backoff_max_s=0.05
    )
    await worker._tick()

    assert len(transport.acked) == 1
    acked = transport.acked[0]
    # The relay keys outcomes by session to fold them into the right snapshot.
    assert acked["session_id"] == snapshot.session_id
    assert acked["status"] == "applied"
    assert set(acked) >= set(VisitorActionOutcome.model_fields) | {"session_id"}
