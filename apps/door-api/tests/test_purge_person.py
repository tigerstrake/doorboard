"""Unenrollment erasure on door-api (audit backlog: purge-chain gap).

door-api's social rows carry a ``person_id`` (guestbook entries, poll votes, check-ins), but
there was no way to erase them when a person is unenrolled — the moderation "delete" is a
soft-delete that keeps the text, an audit tool, not an erasure. ``SocialStore.purge_person``
hard-deletes them and ``POST /internal/purge/{person_id}`` exposes it to the (token-protected)
Pi-local purge flow.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from door_api.social.store import SocialStore
from fastapi.testclient import TestClient

# The app module constructs `state = DoorApiState()` at import time, which needs a DB path.
os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state  # noqa: E402

TOKEN = "internal-test-token"
_TS = "2026-01-01T00:00:00Z"


# -- store level (no HTTP) --------------------------------------------------


def test_purge_person_hard_deletes_only_that_persons_rows() -> None:
    store = SocialStore(":memory:")
    store.insert_guestbook_entry(
        entry_id="g1",
        text="hi",
        author_label=None,
        status="approved",
        ip_hash="ip",
        session_key_hash="s1",
        created_at=_TS,
        person_id="prs_target",
    )
    store.insert_guestbook_entry(
        entry_id="g2",
        text="hey",
        author_label=None,
        status="approved",
        ip_hash="ip",
        session_key_hash="s2",
        created_at=_TS,
        person_id="prs_other",
    )
    store.insert_guestbook_entry(
        entry_id="g3",
        text="anon",
        author_label=None,
        status="approved",
        ip_hash="ip",
        session_key_hash="s3",
        created_at=_TS,
        person_id=None,
    )
    store.insert_vote(
        poll_id="p1", session_token="t1", option_id="o1", created_at=_TS, person_id="prs_target"
    )
    store.insert_vote(
        poll_id="p1", session_token="t2", option_id="o1", created_at=_TS, person_id=None
    )
    store.insert_checkin(
        checkin_id="c1",
        person_id="prs_target",
        label="Tiger",
        photo_recording_id=None,
        session_key_hash="s",
        created_at=_TS,
    )
    store.insert_checkin(
        checkin_id="c2",
        person_id=None,
        label=None,
        photo_recording_id=None,
        session_key_hash="s",
        created_at=_TS,
    )

    counts = store.purge_person("prs_target")
    assert counts == {"guestbook_entries": 1, "poll_votes": 1, "checkins": 1}

    def n(sql: str, *args: str) -> int:
        return int(store._conn.execute(sql, args).fetchone()[0])

    # The target's rows are gone; the other person's and the anonymous rows survive.
    assert n("SELECT COUNT(*) FROM guestbook_entries WHERE person_id = ?", "prs_target") == 0
    assert n("SELECT COUNT(*) FROM guestbook_entries WHERE person_id = ?", "prs_other") == 1
    assert n("SELECT COUNT(*) FROM guestbook_entries WHERE person_id IS NULL") == 1
    assert n("SELECT COUNT(*) FROM poll_votes WHERE person_id = ?", "prs_target") == 0
    assert n("SELECT COUNT(*) FROM poll_votes WHERE person_id IS NULL") == 1
    assert n("SELECT COUNT(*) FROM checkins WHERE person_id = ?", "prs_target") == 0
    assert n("SELECT COUNT(*) FROM checkins WHERE person_id IS NULL") == 1


def test_purge_person_empty_id_is_a_noop() -> None:
    # An empty person_id must never fall through to matching every anonymous row.
    store = SocialStore(":memory:")
    store.insert_checkin(
        checkin_id="c1",
        person_id=None,
        label=None,
        photo_recording_id=None,
        session_key_hash="s",
        created_at=_TS,
    )
    assert store.purge_person("") == {"guestbook_entries": 0, "poll_votes": 0, "checkins": 0}
    assert int(store._conn.execute("SELECT COUNT(*) FROM checkins").fetchone()[0]) == 1


# -- endpoint --------------------------------------------------------------


@pytest.fixture
def _app_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_INTERNAL_EVENT_TOKEN", TOKEN)
    state.__init__()
    state.startup()
    yield
    state.shutdown()


def test_purge_endpoint_requires_the_internal_token(_app_state: None) -> None:
    client = TestClient(app)
    assert client.post("/internal/purge/prs_x").status_code == 401


def test_purge_endpoint_hard_deletes_the_persons_rows(_app_state: None) -> None:
    state.social_store.insert_checkin(
        checkin_id="c1",
        person_id="prs_target",
        label="Tiger",
        photo_recording_id=None,
        session_key_hash="s",
        created_at=_TS,
    )
    state.social_store.insert_checkin(
        checkin_id="c2",
        person_id=None,
        label=None,
        photo_recording_id=None,
        session_key_hash="s",
        created_at=_TS,
    )

    client = TestClient(app)
    resp = client.post("/internal/purge/prs_target", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["purged"] is True
    assert body["counts"]["checkins"] == 1

    def n(sql: str, *args: str) -> int:
        return int(state.social_store._conn.execute(sql, args).fetchone()[0])

    assert n("SELECT COUNT(*) FROM checkins WHERE person_id = ?", "prs_target") == 0
    assert n("SELECT COUNT(*) FROM checkins WHERE person_id IS NULL") == 1
