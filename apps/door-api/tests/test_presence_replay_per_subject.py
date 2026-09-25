"""Presence is replayed per subject while it is still fresh (ADR-0048).

ADR-0027 kept ``status.*`` out of the replay cache, and the wallboard's primary tile read
"Presence unavailable." after every kiosk relaunch, door-ui restart or reload until
someone's status next changed — hours, on a stable evening. The label a late client would
learn is the one the same corridor was shown a moment earlier, and the control plane
already bounds how long a label may be believed (``CONTROL_PLANE_PRESENCE_STALE_AFTER_S``).

So the last event per ``(type, subject_id)`` is replayed to a connecting client, after the
session snapshot, only while ``now < occurred_at + stale_after`` and, when the event
carries ``until``, ``now < until``. A stale or expired label is not replayed: the tile shows
*unknown*, never the past. The control plane also now publishes each subject on its own
retained topic alongside the legacy one; the two copies share an ``event_id`` and the
bridge deduplicates them.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from doorboard_contracts.examples import example_event
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.ambient_cache import (  # noqa: E402
    DEFAULT_MAX_SUBJECTS,
    DEFAULT_PRESENCE_STALE_AFTER_S,
    AmbientCache,
)
from door_api.app import app, state  # noqa: E402
from door_api.broadcast import DisplayBroadcast  # noqa: E402
from door_api.config import SessionConfig  # noqa: E402
from door_api.mqtt_bridge import MqttBridge  # noqa: E402

T0 = datetime(2026, 9, 24, 12, 0, 0, tzinfo=UTC)


def _presence(
    subject_id: str,
    *,
    label: str = "available",
    occurred_at: datetime = T0,
    until: datetime | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    event = example_event("status.presence_changed").model_dump(mode="json")
    event["occurred_at"] = occurred_at.isoformat()
    event["payload"] = {**event["payload"], "subject_id": subject_id, "label": label}
    event["payload"]["until"] = until.isoformat() if until is not None else None
    if event_id is not None:
        event["event_id"] = event_id
    return event


def _cache(now: datetime, **kwargs: Any) -> AmbientCache:
    return AmbientCache(wall_clock_fn=lambda: now.timestamp(), **kwargs)


def _subjects(events: list[dict[str, Any]]) -> list[str]:
    return [e["payload"]["subject_id"] for e in events if e["type"] == "status.presence_changed"]


# -- the cache ---------------------------------------------------------------------------


def test_the_last_event_per_subject_is_replayed() -> None:
    cache = _cache(T0 + timedelta(minutes=1))
    assert cache.remember(_presence("owner", label="busy")) is True
    assert cache.remember(_presence("roommate", label="sleeping")) is True
    assert cache.remember(_presence("owner", label="available")) is True

    replayed = cache.replay()
    assert _subjects(replayed) == ["roommate", "owner"], "one per subject, oldest first"
    assert [e["payload"]["label"] for e in replayed] == ["sleeping", "available"]


def test_a_stale_label_is_not_replayed() -> None:
    cache = _cache(T0 + timedelta(seconds=DEFAULT_PRESENCE_STALE_AFTER_S + 1))
    cache.remember(_presence("owner"))
    assert cache.replay() == []

    fresh = _cache(T0 + timedelta(seconds=DEFAULT_PRESENCE_STALE_AFTER_S - 1))
    fresh.remember(_presence("owner"))
    assert _subjects(fresh.replay()) == ["owner"]


def test_an_expired_until_is_not_replayed() -> None:
    # "busy until 12:10", asked at 12:11: the label has expired by its own terms even
    # though the event is well inside the staleness window.
    cache = _cache(T0 + timedelta(minutes=11))
    cache.remember(_presence("owner", label="busy", until=T0 + timedelta(minutes=10)))
    assert cache.replay() == []

    before = _cache(T0 + timedelta(minutes=9))
    before.remember(_presence("owner", label="busy", until=T0 + timedelta(minutes=10)))
    assert _subjects(before.replay()) == ["owner"]


def test_an_unreadable_timestamp_fails_toward_not_replaying() -> None:
    cache = _cache(T0)
    broken = _presence("owner")
    broken["occurred_at"] = "yesterday-ish"
    cache.remember(broken)
    assert cache.replay() == []

    bad_until = _presence("roommate")
    bad_until["payload"]["until"] = "soon"
    cache.remember(bad_until)
    assert cache.replay() == []


def test_the_subject_count_is_bounded() -> None:
    # The id comes off a topic (and, upstream, off a webhook); it must not grow this.
    cache = _cache(T0)
    for index in range(DEFAULT_MAX_SUBJECTS + 3):
        cache.remember(_presence(f"subject-{index}"))
    replayed = _subjects(cache.replay())
    assert len(replayed) == DEFAULT_MAX_SUBJECTS
    assert replayed[0] == "subject-3", "the least recently updated subjects are evicted"


def test_presence_without_a_subject_is_skipped_not_raised() -> None:
    cache = _cache(T0)
    nameless = _presence("owner")
    del nameless["payload"]["subject_id"]
    assert cache.remember(nameless) is False
    assert cache.remember({"type": "status.presence_changed", "payload": "?"}) is False
    assert cache.replay() == []


def test_presence_follows_ambient_and_forget_all_clears_both() -> None:
    cache = _cache(T0)
    cache.remember(_presence("owner"))
    cache.remember(example_event("ambient.food_recommendation").model_dump(mode="json"))
    assert [e["type"] for e in cache.replay()] == [
        "ambient.food_recommendation",
        "status.presence_changed",
    ]
    cache.forget_all()
    assert cache.replay() == []


def test_the_default_matches_the_control_planes_staleness_contract() -> None:
    # apps/control-plane-api settings: CONTROL_PLANE_PRESENCE_STALE_AFTER_S = 1800.
    # Restated here on purpose: the point is to fail when the two drift apart.
    assert DEFAULT_PRESENCE_STALE_AFTER_S == 1800.0
    assert SessionConfig(db_path=":memory:").presence_stale_after_s == 1800.0


# -- the bridge --------------------------------------------------------------------------


def _drain(queue: asyncio.Queue[str]) -> list[dict[str, Any]]:
    out = []
    while not queue.empty():
        out.append(json.loads(queue.get_nowait()))
    return out


def test_the_legacy_and_per_subject_copies_reach_a_kiosk_once() -> None:
    cache = _cache(T0 + timedelta(minutes=1))
    broadcast = DisplayBroadcast(replay_source=cache.replay)
    bridge = MqttBridge(url="mqtt://nuc.local:1883", broadcast=broadcast, remember=cache.remember)
    live = broadcast.make_client_queue()
    _drain(live)

    payload = json.dumps(_presence("owner", event_id="0192d4c0-0000-7000-8000-000000000001"))
    assert bridge.handle_payload(payload, topic="doorboard/status/presence_changed") is True
    assert bridge.handle_payload(payload, topic="doorboard/status/presence_changed/owner") is False

    assert bridge.messages_broadcast == 1
    assert bridge.messages_duplicate == 1
    assert bridge.parse_errors == 0
    assert _subjects([m["event"] for m in _drain(live)]) == ["owner"], "one delta, not two"
    # And a late client is told once as well.
    late = broadcast.make_client_queue()
    frames = _drain(late)
    assert frames[0]["type"] == "snapshot"
    assert _subjects([m["event"] for m in frames[1:]]) == ["owner"]


def test_a_broker_reconnect_redelivering_retained_messages_is_not_a_second_change() -> None:
    cache = _cache(T0 + timedelta(minutes=1))
    broadcast = DisplayBroadcast(replay_source=cache.replay)
    bridge = MqttBridge(url="mqtt://nuc.local:1883", broadcast=broadcast, remember=cache.remember)
    first = json.dumps(_presence("owner", event_id="0192d4c0-0000-7000-8000-000000000002"))
    second = json.dumps(_presence("roommate", event_id="0192d4c0-0000-7000-8000-000000000003"))
    for _ in range(2):  # connect, reconnect
        bridge.handle_payload(first)
        bridge.handle_payload(second)
    assert bridge.messages_broadcast == 2
    assert bridge.messages_duplicate == 2


# -- the /ws hub -----------------------------------------------------------------------


@pytest.fixture
def _app_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    state.__init__()
    state.startup()
    yield
    state.shutdown()


def test_a_new_ws_client_receives_the_fresh_presence_of_each_subject(_app_state: None) -> None:
    now = datetime.now(UTC)
    assert state.ambient_cache.remember(_presence("owner", label="busy", occurred_at=now))
    assert state.ambient_cache.remember(_presence("roommate", label="away", occurred_at=now))
    # A third subject whose label is long past: not replayed.
    assert state.ambient_cache.remember(_presence("guest", occurred_at=now - timedelta(hours=2)))

    client = TestClient(app)
    with client.websocket_connect("/ws") as websocket:
        assert json.loads(websocket.receive_text())["type"] == "snapshot"
        first = json.loads(websocket.receive_text())
        second = json.loads(websocket.receive_text())

    assert first["type"] == second["type"] == "delta"
    assert {first["event"]["payload"]["subject_id"], second["event"]["payload"]["subject_id"]} == {
        "owner",
        "roommate",
    }
    assert state.ambient_cache.replay()[-1]["payload"]["subject_id"] != "guest"
