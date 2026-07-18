"""Notification rule evaluation and cooldown behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from control_plane_api.notify import (
    MultiNotifier,
    NotifyEngine,
    NtfyNotifier,
    NullNotifier,
    RecordingNotifier,
    TelegramNotifier,
    build_notifier,
    evaluate_rules,
)
from doorboard_contracts import parse_event

from .factories import build_event

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)
MILE_KM = 1.60934


def _aircraft_event(nearby: list[dict]):
    return parse_event(
        build_event("ambient.aircraft_summary", payload_overrides={"nearby": nearby})
    )


def _plane(distance_km: float, altitude_ft: int = 30000, callsign: str = "UAL123") -> dict:
    return {
        "callsign": callsign,
        "altitude_ft": altitude_ft,
        "distance_km": distance_km,
        "heading": 90,
    }


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


# ── aircraft proximity alert (T-610) ───────────────────────────────────────


def test_aircraft_within_radius_triggers_alert() -> None:
    event = _aircraft_event([_plane(distance_km=1.0), _plane(distance_km=20.0)])
    n = evaluate_rules(event, sync_stall_alert_s=14400, aircraft_alert_radius_km=MILE_KM)
    assert n is not None
    assert n.rule_key.startswith("aircraft_overhead:")
    assert n.cooldown_s == 600  # default aircraft cooldown flows through
    assert "UAL123" in n.message


def test_aircraft_outside_radius_does_not_trigger() -> None:
    event = _aircraft_event([_plane(distance_km=3.0)])  # ~1.86 mi > 1 mi
    assert evaluate_rules(event, sync_stall_alert_s=14400, aircraft_alert_radius_km=MILE_KM) is None


def test_aircraft_alert_disabled_when_radius_zero() -> None:
    event = _aircraft_event([_plane(distance_km=0.1)])
    assert evaluate_rules(event, sync_stall_alert_s=14400, aircraft_alert_radius_km=0.0) is None


def test_aircraft_altitude_ceiling_filters_high_cruisers() -> None:
    event = _aircraft_event([_plane(distance_km=0.5, altitude_ft=35000)])
    n = evaluate_rules(
        event,
        sync_stall_alert_s=14400,
        aircraft_alert_radius_km=MILE_KM,
        aircraft_alert_max_altitude_ft=10000,
    )
    assert n is None  # within radius but too high


def test_aircraft_alert_reports_nearest_and_count() -> None:
    event = _aircraft_event(
        [_plane(distance_km=1.4, callsign="FAR1"), _plane(distance_km=0.3, callsign="NEAR1")]
    )
    n = evaluate_rules(event, sync_stall_alert_s=14400, aircraft_alert_radius_km=MILE_KM)
    assert n is not None and "NEAR1" in n.message and "+1 more" in n.message


def test_per_notification_cooldown_overrides_engine_default(session_factory) -> None:
    # Aircraft uses a short cooldown even though the engine default is long.
    notifier = RecordingNotifier()
    engine = NotifyEngine(
        notifier,
        cooldown_s=3600,
        sync_stall_alert_s=14400,
        aircraft_alert_radius_km=MILE_KM,
        aircraft_alert_cooldown_s=300,
    )
    event = _aircraft_event([_plane(distance_km=0.5)])

    with session_factory() as session:
        engine.on_event(session, event, now=NOW)
        session.commit()
    with session_factory() as session:  # 6 min later > 300s aircraft cooldown
        engine.on_event(session, event, now=NOW + timedelta(minutes=6))
        session.commit()

    assert len(notifier.sent) == 2  # would be 1 under the 3600s engine default


# ── channel routing ────────────────────────────────────────────────────────


def test_build_notifier_none_configured_is_null() -> None:
    assert isinstance(build_notifier(), NullNotifier)


def test_build_notifier_ntfy_only() -> None:
    n = build_notifier(ntfy_url="https://ntfy.sh", ntfy_topic="t")
    assert isinstance(n, NtfyNotifier)


def test_build_notifier_telegram_only() -> None:
    n = build_notifier(telegram_bot_token="tok", telegram_chat_ids=["1"])
    assert isinstance(n, TelegramNotifier)


def test_build_notifier_both_is_multi() -> None:
    n = build_notifier(
        ntfy_url="https://ntfy.sh",
        ntfy_topic="t",
        telegram_bot_token="tok",
        telegram_chat_ids=["1"],
    )
    assert isinstance(n, MultiNotifier)


def test_telegram_notifier_sends_message(monkeypatch) -> None:
    import httpx
    from control_plane_api.notify import Notification

    calls: list[dict] = []

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {"ok": True}

    monkeypatch.setattr(
        httpx, "post", lambda url, **kw: calls.append({"url": url, **kw}) or _Resp()
    )
    TelegramNotifier(bot_token="tok", chat_ids=["42"], api_base_url="https://tg.example").notify(
        Notification(rule_key="aircraft_overhead:primary", title="Plane overhead", message="hi")
    )
    assert len(calls) == 1
    assert calls[0]["url"] == "https://tg.example/bottok/sendMessage"
    assert calls[0]["data"]["chat_id"] == "42"
    assert "Plane overhead" in calls[0]["data"]["text"]
