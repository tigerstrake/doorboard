"""Presence engine at the DB-session layer (no HTTP): calendar wiring,
tracking_enabled gating, change-only emission, retention cap.

`test_presence_resolver.py` covers pure precedence/expiry logic;
`test_presence_http.py` covers the HTTP surface. This file exercises
`presence_engine`'s DB-backed orchestration directly via `session_factory`,
the same way `test_ingest_idempotency.py` exercises `ingest_batch` — no
FastAPI/TestClient involved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from control_plane_api import presence_engine
from control_plane_api.db import session_scope
from control_plane_api.models import PresenceHistoryRow
from control_plane_api.mqtt import RecordingMqttPublisher
from control_plane_api.presence import MockCalendarProvider, SourceEntry
from doorboard_contracts import PresenceLabel
from sqlalchemy import select

NOW = datetime.now(UTC)


def _history_rows(session_factory, subject_id: str) -> list[PresenceHistoryRow]:
    with session_factory() as session:
        stmt = (
            select(PresenceHistoryRow)
            .where(PresenceHistoryRow.subject_id == subject_id)
            .order_by(PresenceHistoryRow.occurred_at)
        )
        return list(session.execute(stmt).scalars().all())


def test_sync_presence_emits_once_then_dedupes_on_repeat_resolves(session_factory) -> None:
    mqtt = RecordingMqttPublisher()
    calendar = MockCalendarProvider()
    subject_id = "owner"

    with session_scope(session_factory) as session:
        first = presence_engine.sync_presence(
            session,
            subject_id=subject_id,
            now=NOW,
            door_id="primary",
            calendar_provider=calendar,
            mqtt_publisher=mqtt,
            history_max_rows=500,
        )
    assert first.label == PresenceLabel.UNKNOWN
    assert first.source == "default"
    assert len(mqtt.published) == 1
    assert len(_history_rows(session_factory, subject_id)) == 1

    # Resolving again with nothing changed must not emit a second event —
    # this is the "change-only" contract the brief requires.
    with session_scope(session_factory) as session:
        second = presence_engine.sync_presence(
            session,
            subject_id=subject_id,
            now=NOW + timedelta(seconds=5),
            door_id="primary",
            calendar_provider=calendar,
            mqtt_publisher=mqtt,
            history_max_rows=500,
        )
    assert second == first
    assert len(mqtt.published) == 1
    assert len(_history_rows(session_factory, subject_id)) == 1


def test_manual_override_write_triggers_new_history_row(session_factory) -> None:
    mqtt = RecordingMqttPublisher()
    calendar = MockCalendarProvider()
    subject_id = "owner"

    with session_scope(session_factory) as session:
        presence_engine.sync_presence(
            session,
            subject_id=subject_id,
            now=NOW,
            door_id="primary",
            calendar_provider=calendar,
            mqtt_publisher=mqtt,
            history_max_rows=500,
        )
        presence_engine.set_manual_override(
            session, subject_id=subject_id, label=PresenceLabel.BUSY, until=None, now=NOW
        )
        resolved = presence_engine.sync_presence(
            session,
            subject_id=subject_id,
            now=NOW,
            door_id="primary",
            calendar_provider=calendar,
            mqtt_publisher=mqtt,
            history_max_rows=500,
        )
    assert resolved.label == PresenceLabel.BUSY
    assert resolved.source == "manual"
    assert len(mqtt.published) == 2
    rows = _history_rows(session_factory, subject_id)
    assert [r.label for r in rows] == ["unknown", "busy"]
    assert [r.source for r in rows] == ["default", "manual"]


def test_manual_override_expiry_falls_through_to_next_source(session_factory) -> None:
    mqtt = RecordingMqttPublisher()
    calendar = MockCalendarProvider()
    subject_id = "roommate"
    past = NOW - timedelta(minutes=1)

    with session_scope(session_factory) as session:
        presence_engine.set_source_value(
            session,
            subject_id=subject_id,
            source="geofence_label",
            label=PresenceLabel.AT_LIBRARY,
            until=None,
            now=NOW,
        )
        presence_engine.set_manual_override(
            session, subject_id=subject_id, label=PresenceLabel.BUSY, until=past, now=NOW
        )
        resolved = presence_engine.sync_presence(
            session,
            subject_id=subject_id,
            now=NOW,
            door_id="primary",
            calendar_provider=calendar,
            mqtt_publisher=mqtt,
            history_max_rows=500,
        )
    assert resolved.label == PresenceLabel.AT_LIBRARY
    assert resolved.source == "geofence_label"


def test_calendar_provider_wins_when_nothing_higher_precedence_present(session_factory) -> None:
    mqtt = RecordingMqttPublisher()
    calendar = MockCalendarProvider()
    subject_id = "owner"
    calendar.set_canned(subject_id, SourceEntry(PresenceLabel.AT_CLASS, until=None))

    with session_scope(session_factory) as session:
        resolved = presence_engine.sync_presence(
            session,
            subject_id=subject_id,
            now=NOW,
            door_id="primary",
            calendar_provider=calendar,
            mqtt_publisher=mqtt,
            history_max_rows=500,
        )
    assert resolved.label == PresenceLabel.AT_CLASS
    assert resolved.source == "calendar"


def test_calendar_disabled_per_source_is_skipped(session_factory) -> None:
    mqtt = RecordingMqttPublisher()
    calendar = MockCalendarProvider()
    subject_id = "owner"
    calendar.set_canned(subject_id, SourceEntry(PresenceLabel.AT_CLASS, until=None))

    with session_scope(session_factory) as session:
        presence_engine.set_source_enabled(
            session, subject_id=subject_id, source="calendar", enabled=False, now=NOW
        )
        resolved = presence_engine.sync_presence(
            session,
            subject_id=subject_id,
            now=NOW,
            door_id="primary",
            calendar_provider=calendar,
            mqtt_publisher=mqtt,
            history_max_rows=500,
        )
    assert resolved.source == "default"
    assert resolved.label == PresenceLabel.UNKNOWN


def test_tracking_disabled_suppresses_inferred_sources_but_not_manual(session_factory) -> None:
    mqtt = RecordingMqttPublisher()
    calendar = MockCalendarProvider()
    subject_id = "roommate"
    calendar.set_canned(subject_id, SourceEntry(PresenceLabel.AT_CLASS, until=None))

    with session_scope(session_factory) as session:
        presence_engine.set_source_value(
            session,
            subject_id=subject_id,
            source="geofence_label",
            label=PresenceLabel.AT_LIBRARY,
            until=None,
            now=NOW,
        )
        presence_engine.set_subject_tracking_enabled(
            session, subject_id=subject_id, enabled=False, now=NOW
        )
        resolved_without_manual = presence_engine.sync_presence(
            session,
            subject_id=subject_id,
            now=NOW,
            door_id="primary",
            calendar_provider=calendar,
            mqtt_publisher=mqtt,
            history_max_rows=500,
        )
        assert resolved_without_manual.source == "default"

        presence_engine.set_manual_override(
            session, subject_id=subject_id, label=PresenceLabel.SLEEPING, until=None, now=NOW
        )
        resolved_with_manual = presence_engine.sync_presence(
            session,
            subject_id=subject_id,
            now=NOW,
            door_id="primary",
            calendar_provider=calendar,
            mqtt_publisher=mqtt,
            history_max_rows=500,
        )
    assert resolved_with_manual.label == PresenceLabel.SLEEPING
    assert resolved_with_manual.source == "manual"


def test_retention_cap_keeps_only_the_most_recent_rows(session_factory) -> None:
    mqtt = RecordingMqttPublisher()
    calendar = MockCalendarProvider()
    subject_id = "owner"
    labels = [
        PresenceLabel.BUSY,
        PresenceLabel.AVAILABLE,
        PresenceLabel.SLEEPING,
        PresenceLabel.AWAY,
        PresenceLabel.AT_CLASS,
        PresenceLabel.AT_LIBRARY,
    ]
    with session_scope(session_factory) as session:
        for i, label in enumerate(labels):
            moment = NOW + timedelta(seconds=i)
            presence_engine.set_manual_override(
                session, subject_id=subject_id, label=label, until=None, now=moment
            )
            presence_engine.sync_presence(
                session,
                subject_id=subject_id,
                now=moment,
                door_id="primary",
                calendar_provider=calendar,
                mqtt_publisher=mqtt,
                history_max_rows=3,
            )
    rows = _history_rows(session_factory, subject_id)
    assert len(rows) == 3
    assert [r.label for r in rows] == [label.value for label in labels[-3:]]


def test_list_known_subject_ids_always_includes_the_default_pair(session_factory) -> None:
    with session_factory() as session:
        ids = presence_engine.list_known_subject_ids(session)
    assert ids == ["owner", "roommate"]

    with session_scope(session_factory) as session:
        presence_engine.set_source_value(
            session,
            subject_id="guest",
            source="manual",
            label=PresenceLabel.AVAILABLE,
            until=None,
            now=NOW,
        )
    with session_factory() as session:
        ids = presence_engine.list_known_subject_ids(session)
    assert ids == ["owner", "roommate", "guest"]
