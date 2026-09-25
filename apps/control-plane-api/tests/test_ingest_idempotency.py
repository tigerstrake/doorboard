"""Idempotent ingestion: replaying the same batch N times leaves DB state unchanged.

Acceptance criterion (T-501 brief): "Replay the same batch 10x -> identical
DB state." Covers both the raw event store (event_id uniqueness) and mirror
projections (must not double-apply on replay).
"""

from __future__ import annotations

from datetime import UTC, datetime

from control_plane_api.fanout import FanoutWorker
from control_plane_api.models import EventRow, MediaMirrorRow, SessionMirrorRow, SocialItemRow
from control_plane_api.mqtt import RecordingMqttPublisher
from control_plane_api.notify import NotifyEngine, RecordingNotifier
from control_plane_api.service import ingest_batch
from sqlalchemy import func, select

from .factories import build_event, uuid7


def _snapshot(session_factory) -> dict:
    with session_factory() as session:
        events = session.execute(select(func.count()).select_from(EventRow)).scalar_one()
        sessions = {
            row.session_id: (row.state, row.entry, row.outcome)
            for row in session.execute(select(SessionMirrorRow)).scalars().all()
        }
        media = {
            row.recording_id: (row.path, row.duration_s, row.sync_status)
            for row in session.execute(select(MediaMirrorRow)).scalars().all()
        }
    return {"event_count": events, "sessions": sessions, "media": media}


def test_replaying_a_batch_ten_times_is_a_no_op_after_the_first(session_factory) -> None:
    session_id = str(uuid7())
    recording_id = str(uuid7())
    batch = [
        build_event(
            "session.started", payload_overrides={"session_id": session_id, "entry": "button"}
        ),
        build_event(
            "media.recording_started",
            payload_overrides={"recording_id": recording_id, "session_id": session_id},
        ),
        build_event("media.recording_finalized", payload_overrides={"recording_id": recording_id}),
        build_event(
            "session.ended", payload_overrides={"session_id": session_id, "outcome": "answered"}
        ),
    ]

    mqtt = RecordingMqttPublisher()
    notify = NotifyEngine(RecordingNotifier(), cooldown_s=3600, sync_stall_alert_s=14400)
    now = datetime.now(UTC)

    first_results = ingest_batch(
        session_factory,
        batch,
        batch_id="b1",
        now=now,
        mqtt_publisher=mqtt,
        fanout=FanoutWorker(session_factory, notify_engine=notify),
    )
    assert [r["status"] for r in first_results] == ["stored", "stored", "stored", "stored"]
    snapshot_after_first = _snapshot(session_factory)
    assert snapshot_after_first["event_count"] == 4
    published_after_first = len(mqtt.published)

    for _ in range(10):
        results = ingest_batch(
            session_factory,
            batch,
            batch_id="b1-replay",
            now=now,
            mqtt_publisher=mqtt,
            fanout=FanoutWorker(session_factory, notify_engine=notify),
        )
        assert [r["status"] for r in results] == ["duplicate"] * 4
        assert _snapshot(session_factory) == snapshot_after_first

    # Duplicates must never re-trigger fan-out.
    assert len(mqtt.published) == published_after_first


def test_duplicate_events_across_separate_batches_are_still_caught(session_factory) -> None:
    event = build_event("system.service_health")
    mqtt = RecordingMqttPublisher()
    notify = NotifyEngine(RecordingNotifier(), cooldown_s=3600, sync_stall_alert_s=14400)
    now = datetime.now(UTC)

    r1 = ingest_batch(
        session_factory,
        [event],
        batch_id="a",
        now=now,
        mqtt_publisher=mqtt,
        fanout=FanoutWorker(session_factory, notify_engine=notify),
    )
    r2 = ingest_batch(
        session_factory,
        [event],
        batch_id="b",
        now=now,
        mqtt_publisher=mqtt,
        fanout=FanoutWorker(session_factory, notify_engine=notify),
    )

    assert r1[0]["status"] == "stored"
    assert r2[0]["status"] == "duplicate"
    with session_factory() as session:
        count = session.execute(select(func.count()).select_from(EventRow)).scalar_one()
    assert count == 1


def test_malformed_event_is_rejected_without_touching_other_batch_items(session_factory) -> None:
    good = build_event("system.service_health")
    bad = {"type": "system.service_health", "payload": {"service": "x"}}  # missing envelope fields
    mqtt = RecordingMqttPublisher()
    notify = NotifyEngine(RecordingNotifier(), cooldown_s=3600, sync_stall_alert_s=14400)
    now = datetime.now(UTC)

    results = ingest_batch(
        session_factory,
        [good, bad],
        batch_id="c",
        now=now,
        mqtt_publisher=mqtt,
        fanout=FanoutWorker(session_factory, notify_engine=notify),
    )
    assert results[0]["status"] == "stored"
    assert results[1]["status"] == "rejected"
    assert "error" in results[1]

    with session_factory() as session:
        count = session.execute(select(func.count()).select_from(EventRow)).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# The database refusing a contract-valid event is a `rejected` result, not a 500
# ---------------------------------------------------------------------------


def _worker(session_factory) -> FanoutWorker:
    return FanoutWorker(
        session_factory,
        notify_engine=NotifyEngine(RecordingNotifier(), cooldown_s=3600, sync_stall_alert_s=14400),
    )


def test_a_nul_byte_is_stripped_before_validation_and_the_event_is_stored(
    session_factory,
) -> None:
    """Postgres text cannot hold U+0000; a NUL in a guestbook note is never content."""
    entry = build_event("social.guestbook_entry_created", payload_overrides={"text": "hi\x00there"})
    results = ingest_batch(
        session_factory,
        [entry],
        batch_id="nul",
        now=datetime.now(UTC),
        mqtt_publisher=RecordingMqttPublisher(),
        fanout=_worker(session_factory),
    )
    assert results[0]["status"] == "stored", results
    with session_factory() as session:
        row = session.get(SocialItemRow, ("guestbook", entry["payload"]["entry_id"]))
        stored = session.get(EventRow, entry["event_id"])
    assert row is not None and row.text == "hithere"
    assert stored is not None and "\x00" not in stored.payload["text"]


def test_a_value_the_database_refuses_is_rejected_and_the_rest_of_the_batch_lands(
    session_factory,
) -> None:
    """`social_items.text` is String(2048); the contract does not bound `text`.

    The insert of the raw row succeeds and the mirror projection raises DataError.
    That used to propagate as a 500 — door-sync treated it as transient and burned
    its retry budget on an event that could never land. Now: `rejected`, with the
    raw row rolled back too, and the neighbours stored.
    """
    good_before = build_event("system.service_health")
    oversized = build_event(
        "social.guestbook_entry_created", payload_overrides={"text": "x" * 3000}
    )
    good_after = build_event("system.service_health")
    results = ingest_batch(
        session_factory,
        [good_before, oversized, good_after],
        batch_id="refused",
        now=datetime.now(UTC),
        mqtt_publisher=RecordingMqttPublisher(),
        fanout=_worker(session_factory),
    )
    assert [r["status"] for r in results] == ["stored", "rejected", "stored"]
    assert results[1]["error"].startswith("database refused the event (DataError")
    assert "xxxx" not in results[1]["error"]  # never echoes the content
    with session_factory() as session:
        assert session.get(EventRow, oversized["event_id"]) is None  # rolled back with the mirror
        assert session.get(SocialItemRow, ("guestbook", oversized["payload"]["entry_id"])) is None
        count = session.execute(select(func.count()).select_from(EventRow)).scalar_one()
    assert count == 2
