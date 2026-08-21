"""The last-ambient-value cache, and its replay to a reconnecting /ws client.

The bug this exists to prevent: the dining recommendation publishes once a day, the control
plane publishes to MQTT without `retain`, and the wallboard populates its tiles only from
live events. So any reload — kiosk restart, refresh — left the dining tile blank for up to 24
hours, with the event having been produced and delivered perfectly to a browser that had
already gone.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from door_api.ambient_cache import AmbientCache
from door_api.broadcast import DisplayBroadcast
from door_api.mqtt_bridge import MqttBridge
from doorboard_contracts.examples import example_event


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _event(event_type: str) -> dict[str, Any]:
    return example_event(event_type).model_dump(mode="json")


def test_remembers_the_latest_event_of_each_type() -> None:
    cache = AmbientCache()
    assert cache.remember(_event("ambient.food_recommendation")) is True
    assert cache.remember(_event("ambient.aircraft_summary")) is True

    replayed = {event["type"] for event in cache.replay()}
    assert replayed == {"ambient.food_recommendation", "ambient.aircraft_summary"}


def test_a_newer_event_replaces_the_older_one_of_the_same_type() -> None:
    cache = AmbientCache()
    first = _event("ambient.food_recommendation")
    first["payload"]["title"] = "yesterday"
    second = _event("ambient.food_recommendation")
    second["payload"]["title"] = "today"

    cache.remember(first)
    cache.remember(second)

    assert len(cache) == 1
    assert cache.replay()[0]["payload"]["title"] == "today"


def test_presence_is_deliberately_not_cached() -> None:
    # status.* carries presence: personal, with its own staleness contract on the control
    # plane. Caching it would turn a live-only signal into a remembered one, telling a
    # late-connecting client where someone was minutes ago.
    cache = AmbientCache()
    assert cache.remember(_event("status.presence_changed")) is False
    assert cache.replay() == []


def test_an_event_without_a_type_is_skipped_not_raised() -> None:
    # Runs on the bridge's read loop; an exception here would force a broker reconnect.
    cache = AmbientCache()
    assert cache.remember({"payload": {}}) is False
    assert cache.remember({"type": 42}) is False  # type: ignore[dict-item]
    assert cache.replay() == []


def test_entries_expire_so_stale_readings_are_not_replayed_as_current() -> None:
    # After a multi-day producer outage the tile must come back empty rather than
    # confidently serving last Tuesday's lunch.
    clock = Clock()
    cache = AmbientCache(max_age_s=100.0, monotonic_fn=clock)
    cache.remember(_event("ambient.food_recommendation"))

    clock.now += 99.0
    assert len(cache.replay()) == 1

    clock.now += 2.0
    assert cache.replay() == []


def test_the_type_count_is_bounded() -> None:
    # The type comes off an MQTT topic, so a renamed or misbehaving producer must not be
    # able to grow this without bound.
    cache = AmbientCache(max_types=2)
    for index in range(5):
        cache.remember({"type": f"ambient.made_up_{index}", "payload": {}})

    remaining = [event["type"] for event in cache.replay()]
    assert remaining == ["ambient.made_up_3", "ambient.made_up_4"], "oldest types evicted first"


class FakeBroadcastWithCache:
    """The real DisplayBroadcast, wired to a real cache — the seam under test."""

    def __init__(self) -> None:
        self.cache = AmbientCache()
        self.broadcast = DisplayBroadcast(replay_source=self.cache.replay)


def _drain(queue: asyncio.Queue[str]) -> list[dict[str, Any]]:
    messages = []
    while not queue.empty():
        messages.append(json.loads(queue.get_nowait()))
    return messages


def test_a_reconnecting_client_is_told_the_ambient_event_it_missed() -> None:
    wired = FakeBroadcastWithCache()
    bridge = MqttBridge(
        url="mqtt://nuc.local:1883",
        broadcast=wired.broadcast,
        remember=wired.cache.remember,
    )

    # The once-a-day event arrives while nobody is watching.
    assert bridge.handle_payload(example_event("ambient.food_recommendation").model_dump_json())
    assert wired.broadcast.client_count == 0

    # Now the wallboard reloads and connects.
    queue = wired.broadcast.make_client_queue()
    messages = _drain(queue)

    assert messages[0]["type"] == "snapshot", "the session snapshot must come first"
    replayed = [m for m in messages if m["type"] == "delta"]
    assert [m["event"]["type"] for m in replayed] == ["ambient.food_recommendation"]
    # The identical envelope live events use, so the UI needs no separate code path.
    assert "payload" in replayed[0]["event"]


def test_a_client_connecting_with_nothing_cached_gets_only_the_snapshot() -> None:
    wired = FakeBroadcastWithCache()
    messages = _drain(wired.broadcast.make_client_queue())
    assert [m["type"] for m in messages] == ["snapshot"]


def test_a_broken_replay_source_still_lets_a_client_connect() -> None:
    # Best-effort: the wallboard's ambient tiles are a nice-to-have, connecting is not.
    def boom() -> list[dict[str, Any]]:
        raise RuntimeError("cache exploded")

    broadcast = DisplayBroadcast(replay_source=boom)
    messages = _drain(broadcast.make_client_queue())
    assert [m["type"] for m in messages] == ["snapshot"]
    assert broadcast.client_count == 1


@pytest.mark.parametrize("event_type", ["ambient.food_recommendation", "ambient.satellite_pass"])
def test_the_low_frequency_channels_are_covered(event_type: str) -> None:
    # These are the two that motivated this: 86400 s and 3600 s producer intervals.
    cache = AmbientCache()
    assert cache.remember(_event(event_type)) is True
