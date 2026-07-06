"""Idempotent ingestion: replaying the same batch N times leaves DB state unchanged.

Acceptance criterion (T-501 brief): "Replay the same batch 10x -> identical
DB state." Covers both the raw event store (event_id uniqueness) and mirror
projections (must not double-apply on replay).
"""

from __future__ import annotations

from datetime import UTC, datetime

from control_plane_api.models import EventRow, MediaMirrorRow, SessionMirrorRow
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
        session_factory, batch, batch_id="b1", now=now, mqtt_publisher=mqtt, notify_engine=notify
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
            notify_engine=notify,
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
        session_factory, [event], batch_id="a", now=now, mqtt_publisher=mqtt, notify_engine=notify
    )
    r2 = ingest_batch(
        session_factory, [event], batch_id="b", now=now, mqtt_publisher=mqtt, notify_engine=notify
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
        notify_engine=notify,
    )
    assert results[0]["status"] == "stored"
    assert results[1]["status"] == "rejected"
    assert "error" in results[1]

    with session_factory() as session:
        count = session.execute(select(func.count()).select_from(EventRow)).scalar_one()
    assert count == 1
