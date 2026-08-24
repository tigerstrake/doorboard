"""Arrival log (ADR-0018 P-26, P-27, P-28).

The load-bearing claim is the deletion one: this log lives in the enrollment
database precisely so that revoking consent destroys arrival history in the same
transaction as the face templates. Retention is unbounded, so if that cascade ever
broke, someone who unenrolled would leave a permanent record of every time they
came to the door — and nothing would surface it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from door_visiond.embedder import MockEmbedder
from door_visiond.enrollment import EnrollmentStore, ProfileSpec
from door_visiond.settings import Settings
from fastapi.testclient import TestClient

from .conftest import CONSENT_VERSION, TEST_DIM

MERGE_WINDOW_S = 600.0


def _enrol(store: EnrollmentStore, name: str, profile_id: str) -> str:
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(name.encode())
    return store.enroll(
        display_name=name,
        consent_version=CONSENT_VERSION,
        consent_at=datetime.now(UTC),
        embeddings=[(emb, "mock", 0.9)],
        profile=ProfileSpec(profile_id=profile_id, color="#ffb300"),
    )


@pytest.fixture
def store(ssd_settings: Settings) -> EnrollmentStore:
    return EnrollmentStore(ssd_settings.enrollment_db_path)


# -- P-27: visits are merged, not accumulated ------------------------------


def test_sightings_within_the_window_extend_one_visit(store: EnrollmentStore) -> None:
    person_id = _enrol(store, "Tiger", "sunrise")
    start = datetime(2026, 8, 5, 18, 0, tzinfo=UTC)

    first_id, opened = store.record_sighting(person_id, now=start, merge_window_s=MERGE_WINDOW_S)
    assert opened is True

    # Still there five minutes later — same visit, extended.
    second_id, opened_again = store.record_sighting(
        person_id, now=start + timedelta(minutes=5), merge_window_s=MERGE_WINDOW_S
    )
    assert (second_id, opened_again) == (first_id, False)

    visits = store.list_visits()
    assert len(visits) == 1
    assert visits[0]["arrived_at"] == start.isoformat()
    assert visits[0]["last_seen_at"] == (start + timedelta(minutes=5)).isoformat()


def test_a_sighting_after_the_window_opens_a_new_visit(store: EnrollmentStore) -> None:
    person_id = _enrol(store, "Tiger", "sunrise")
    start = datetime(2026, 8, 5, 18, 0, tzinfo=UTC)

    first_id, _ = store.record_sighting(person_id, now=start, merge_window_s=MERGE_WINDOW_S)
    later_id, opened = store.record_sighting(
        person_id, now=start + timedelta(seconds=MERGE_WINDOW_S + 1), merge_window_s=MERGE_WINDOW_S
    )

    assert opened is True
    assert later_id != first_id
    assert len(store.list_visits()) == 2


def test_pacing_in_the_hallway_is_one_visit(store: EnrollmentStore) -> None:
    """The realistic case: recognition refreshes repeatedly while someone lingers."""
    person_id = _enrol(store, "Tiger", "sunrise")
    start = datetime(2026, 8, 5, 18, 0, tzinfo=UTC)
    for seconds in range(0, 300, 30):
        store.record_sighting(
            person_id, now=start + timedelta(seconds=seconds), merge_window_s=MERGE_WINDOW_S
        )
    assert len(store.list_visits()) == 1


def test_two_people_get_independent_visits(store: EnrollmentStore) -> None:
    tiger = _enrol(store, "Tiger", "sunrise")
    alex = _enrol(store, "Alex", "blue_wave")
    now = datetime.now(UTC)
    store.record_sighting(tiger, now=now, merge_window_s=MERGE_WINDOW_S)
    store.record_sighting(alex, now=now, merge_window_s=MERGE_WINDOW_S)

    visits = store.list_visits()
    assert {v["person_id"] for v in visits} == {tiger, alex}
    assert {v["display_name"] for v in visits} == {"Tiger", "Alex"}


def test_a_sighting_for_an_unenrolled_person_is_dropped(store: EnrollmentStore) -> None:
    """The FK would reject it; returning quietly keeps this off the recognition path."""
    visit_id, opened = store.record_sighting(
        "prs_neverenrolled000000", now=datetime.now(UTC), merge_window_s=MERGE_WINDOW_S
    )
    assert (visit_id, opened) == ("", False)
    assert store.list_visits() == []


# -- P-26: unenroll destroys arrival history -------------------------------


def test_unenroll_purges_visit_history(store: EnrollmentStore, ssd_settings: Settings) -> None:
    """P-26: the whole reason this table lives in the enrollment database."""
    person_id = _enrol(store, "Tiger", "sunrise")
    start = datetime(2026, 8, 5, 18, 0, tzinfo=UTC)
    for day in range(5):
        store.record_sighting(
            person_id, now=start + timedelta(days=day), merge_window_s=MERGE_WINDOW_S
        )
    visit_ids = [str(v["visit_id"]) for v in store.list_visits()]
    assert len(visit_ids) == 5

    assert store.unenroll(person_id) is True

    assert store.list_visits() == []
    assert store.visit_counts() == []

    # The bytes are gone, not merely unreferenced — same guarantee as P-5.
    #
    # Scanned for visit ids rather than the person id: ADR-0009 §3 deliberately
    # RETAINS the opaque person id in the revocation tombstone, so asserting its
    # absence would contradict the design (and the tombstone check below). The
    # visit rows are what must be zeroed.
    blob = b""
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(ssd_settings.enrollment_db_path) + suffix)
        if path.exists():
            blob += path.read_bytes()
    for visit_id in visit_ids:
        assert visit_id.encode() not in blob, f"visit row {visit_id} survived unenrollment"

    # The tombstone remains: "was this person ever enrolled" stays answerable
    # without retaining anything biometric, nominal, or locational.
    tombstone = store.get_tombstone(person_id)
    assert tombstone is not None
    assert not hasattr(tombstone, "arrived_at")


def test_purging_visits_keeps_the_person_enrolled(store: EnrollmentStore) -> None:
    """The narrower control: forget where I've been, keep recognising me."""
    person_id = _enrol(store, "Tiger", "sunrise")
    store.record_sighting(person_id, now=datetime.now(UTC), merge_window_s=MERGE_WINDOW_S)

    assert store.purge_visits(person_id=person_id) == 1
    assert store.list_visits() == []
    assert store.person_count() == 1
    assert store.embedding_count() == 1


def test_purging_all_visits_leaves_everyone_enrolled(store: EnrollmentStore) -> None:
    tiger = _enrol(store, "Tiger", "sunrise")
    alex = _enrol(store, "Alex", "blue_wave")
    now = datetime.now(UTC)
    store.record_sighting(tiger, now=now, merge_window_s=MERGE_WINDOW_S)
    store.record_sighting(alex, now=now, merge_window_s=MERGE_WINDOW_S)

    assert store.purge_visits() == 2
    assert store.list_visits() == []
    assert store.person_count() == 2


def test_visit_counts_rank_by_frequency(store: EnrollmentStore) -> None:
    tiger = _enrol(store, "Tiger", "sunrise")
    alex = _enrol(store, "Alex", "blue_wave")
    start = datetime(2026, 8, 5, 18, 0, tzinfo=UTC)
    for day in range(3):
        store.record_sighting(tiger, now=start + timedelta(days=day), merge_window_s=MERGE_WINDOW_S)
    store.record_sighting(alex, now=start, merge_window_s=MERGE_WINDOW_S)

    counts = store.visit_counts()
    assert [(c["display_name"], c["visits"]) for c in counts] == [("Tiger", 3), ("Alex", 1)]


# -- P-28: the log is admin-only, never public -----------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/visits"), ("get", "/visits/counts"), ("post", "/visits/purge")],
)
def test_visit_routes_require_admin_auth(ssd_settings: Settings, method: str, path: str) -> None:
    """P-28: presence data must never be readable from a public route (E-24)."""
    from door_visiond.app import app
    from door_visiond.settings import override_settings, reset_settings

    override_settings(ssd_settings)
    try:
        with TestClient(app) as anon:
            resp = getattr(anon, method)(path)
        assert resp.status_code == 401, f"{method.upper()} {path} was readable anonymously"
    finally:
        reset_settings()


def test_visits_endpoint_returns_history_for_an_admin(client: TestClient) -> None:
    files = [("images", ("a.bin", b"tiger-photo-bytes", "application/octet-stream"))]
    data = {
        "display_name": "Tiger",
        "consent_version": CONSENT_VERSION,
        "consent_confirmed": "true",
        "profile_id": "sunrise",
        "color": "#ffb300",
    }
    person_id = client.post("/enroll", data=data, files=files).json()["person_id"]

    # No visits until recognition sees them.
    assert client.get("/visits").json() == []

    svc = client.app.state.service  # type: ignore[attr-defined]
    svc._store.record_sighting(person_id, now=datetime.now(UTC), merge_window_s=MERGE_WINDOW_S)

    visits = client.get("/visits").json()
    assert len(visits) == 1
    assert visits[0]["display_name"] == "Tiger"
    assert visits[0]["person_id"] == person_id

    # Unenrolling takes the history with it, through the HTTP path too.
    client.post("/unenroll", json={"person_id": person_id})
    assert client.get("/visits").json() == []


def test_the_recognition_path_throttles_visit_writes(ssd_settings: Settings) -> None:
    """A visit log must not put a SQLite write on every cache refresh."""
    from door_visiond.clock import FakeClock
    from door_visiond.service import VisiondService

    clock = FakeClock()
    svc = VisiondService(ssd_settings, clock=clock)
    person_id = _enrol(svc._store, "Tiger", "sunrise")

    for _ in range(20):
        svc._record_visit_sighting(person_id, CONSENT_VERSION)
    # All within the throttle interval, so exactly one write reached the store.
    assert len(svc._store.list_visits()) == 1

    clock.advance(ssd_settings.visit_write_interval_ms + 1)
    svc._record_visit_sighting(person_id, CONSENT_VERSION)
    visits = svc._store.list_visits()
    # Still one visit (inside the merge window), but last_seen_at moved on.
    assert len(visits) == 1
    assert visits[0]["last_seen_at"] != visits[0]["arrived_at"]


# -- the consent gate (ADR-0018) -------------------------------------------


@pytest.mark.parametrize("version", ["v1", "v2", "", None, "banana"])
def test_older_consent_is_not_logged(ssd_settings: Settings, version: str | None) -> None:
    """Someone enrolled before v3 agreed to a greeting, not to being logged.

    Fails closed: an empty or unparseable version logs nothing rather than
    defaulting someone into the arrival log.
    """
    from door_visiond.clock import FakeClock
    from door_visiond.service import VisiondService

    svc = VisiondService(ssd_settings, clock=FakeClock())
    person_id = _enrol(svc._store, "Tiger", "sunrise")

    svc._record_visit_sighting(person_id, version or "")

    assert svc._store.list_visits() == [], f"consent {version!r} should not be logged"


def test_current_consent_is_logged(ssd_settings: Settings) -> None:
    from door_visiond.clock import FakeClock
    from door_visiond.service import VisiondService

    svc = VisiondService(ssd_settings, clock=FakeClock())
    person_id = _enrol(svc._store, "Tiger", "sunrise")

    svc._record_visit_sighting(person_id, CONSENT_VERSION)

    visits = svc._store.list_visits()
    assert len(visits) == 1
    assert visits[0]["person_id"] == person_id


def test_the_consent_version_reaches_the_matcher(ssd_settings: Settings) -> None:
    """The gate is only enforceable if the version travels with the identity."""
    from door_visiond.matcher import Matcher

    store = EnrollmentStore(ssd_settings.enrollment_db_path)
    _enrol(store, "Tiger", "sunrise")

    enrolled = store.load_enrolled()
    assert enrolled[0].consent_version == CONSENT_VERSION

    matcher = Matcher(ssd_settings.match_threshold)
    matcher.refresh(enrolled)
    emb, _q = MockEmbedder(dim=TEST_DIM).embed(b"Tiger")
    result = matcher.match(emb)
    assert result is not None
    assert result.consent_version == CONSENT_VERSION
