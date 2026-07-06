"""Notification rule evaluation and cooldown behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from control_plane_api.notify import NotifyEngine, RecordingNotifier, evaluate_rules
from doorboard_contracts import parse_event

from .factories import build_event

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)


def test_unanswered_session_triggers_missed_bell() -> None:
    event = build_event("session.ended", payload_overrides={"outcome": "unanswered_timeout"})
    notification = evaluate_rules(parse_event(event), sync_stall_alert_s=14400)
    assert notification is not None
    assert notification.rule_key.startswith("missed_bell:")


def test_answered_session_does_not_trigger_missed_bell() -> None:
    event = build_event("session.ended", payload_overrides={"outcome": "answered"})
    assert evaluate_rules(parse_event(event), sync_stall_alert_s=14400) is None


def test_critical_storage_alert_triggers_notification() -> None:
    event = build_event("system.storage_alert", payload_overrides={"severity": "critical"})
    notification = evaluate_rules(parse_event(event), sync_stall_alert_s=14400)
    assert notification is not None
    assert notification.priority == "high"


def test_warning_storage_alert_does_not_trigger() -> None:
    event = build_event("system.storage_alert", payload_overrides={"severity": "warning"})
    assert evaluate_rules(parse_event(event), sync_stall_alert_s=14400) is None


def test_stale_sync_backlog_triggers_notification() -> None:
    event = build_event("media.storage_status", payload_overrides={"oldest_unsynced_s": 20000})
    notification = evaluate_rules(parse_event(event), sync_stall_alert_s=14400)
    assert notification is not None
    assert notification.rule_key.startswith("sync_stalled:")


def test_fresh_sync_backlog_does_not_trigger() -> None:
    event = build_event("media.storage_status", payload_overrides={"oldest_unsynced_s": 10})
    assert evaluate_rules(parse_event(event), sync_stall_alert_s=14400) is None


def test_engine_applies_cooldown_between_repeats(session_factory) -> None:
    notifier = RecordingNotifier()
    engine = NotifyEngine(notifier, cooldown_s=3600, sync_stall_alert_s=14400)
    event = parse_event(
        build_event("system.storage_alert", payload_overrides={"severity": "critical"})
    )

    with session_factory() as session:
        engine.on_event(session, event, now=NOW)
        session.commit()
    with session_factory() as session:
        engine.on_event(session, event, now=NOW + timedelta(minutes=5))
        session.commit()

    assert len(notifier.sent) == 1  # second call within cooldown suppressed


def test_engine_notifies_again_after_cooldown_expires(session_factory) -> None:
    notifier = RecordingNotifier()
    engine = NotifyEngine(notifier, cooldown_s=3600, sync_stall_alert_s=14400)
    event = parse_event(
        build_event("system.storage_alert", payload_overrides={"severity": "critical"})
    )

    with session_factory() as session:
        engine.on_event(session, event, now=NOW)
        session.commit()
    with session_factory() as session:
        engine.on_event(session, event, now=NOW + timedelta(hours=2))
        session.commit()

    assert len(notifier.sent) == 2
