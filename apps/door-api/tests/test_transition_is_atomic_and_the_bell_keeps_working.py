"""One failed SQLite write must not wedge the bell (2026-09 audit, lane A1, P0).

The shape of the bug: the session machine assigned ``_state``, counted the transition
and broadcast it *before* persisting. So one ``sqlite3.OperationalError("database or
disk is full")`` during a hardware press left it in BUTTON_PRESSED — announced to the
kiosks, never written, no timer — and every later press was refused as a double-press.
Worse, the exception ended the ESP32 event consumer task, whose handle kept occupying the
slot so nothing could restart it, and ``/health`` stayed green throughout. On the door
that looks like "the wallboard stopped reacting to the bell", with the ESP32 still
blinking locally.

Three guarantees are pinned here: a transition is all-or-nothing; the consumer survives a
handler exception (and comes back if it dies); and the door says so on ``/health``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sqlite3
from collections.abc import Generator
from typing import Any

import pytest
from doorboard_contracts import SessionState
from doorboard_contracts.examples import example_event
from fastapi.testclient import TestClient

os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import app, state  # noqa: E402
from door_api.config import SessionConfig  # noqa: E402
from door_api.persistence import SessionStore  # noqa: E402
from door_api.session import SessionMachine  # noqa: E402


def _disk_full(*args: Any, **kwargs: Any) -> Any:
    del args, kwargs
    raise sqlite3.OperationalError("database or disk is full")


class _Collector:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def states(self) -> list[str]:
        return [
            e["payload"]["to_state"] for e in self.events if e["type"] == "session.state_changed"
        ]


def _machine(**overrides: Any) -> tuple[SessionMachine, _Collector, SessionStore]:
    store = SessionStore(":memory:")
    collector = _Collector()
    machine = SessionMachine(
        config=SessionConfig(db_path=":memory:", **overrides), store=store, on_event=collector
    )
    return machine, collector, store


# -- the machine never half-applies --------------------------------------------


def test_a_failing_persist_leaves_the_machine_where_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine, collector, store = _machine()
    real_save = store.save_with_delivery_events
    monkeypatch.setattr(store, "save_with_delivery_events", _disk_full)

    with pytest.raises(sqlite3.OperationalError):
        machine.handle_button_pressed()

    # Nothing announced, counted or remembered that is not on disk.
    assert machine.state == SessionState.IDLE
    assert machine.session_id is None
    assert collector.events == []
    assert machine.metrics.transitions == 0
    assert store.load() is None

    # The next press is an ordinary press, not a "double-press during active session".
    monkeypatch.setattr(store, "save_with_delivery_events", real_save)
    assert machine.handle_button_pressed() is True
    assert machine.state == SessionState.VISITOR_MODE
    assert collector.states() == ["BUTTON_PRESSED", "VISITOR_MODE"]
    persisted = store.load()
    assert persisted is not None
    assert persisted.state == SessionState.VISITOR_MODE


def test_a_failing_persist_mid_session_keeps_the_state_and_its_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The timer-driven shape of the same bug: a persist failure on `timeout:ring`
    # used to leave UNANSWERED_TIMEOUT with no offer timer. Here VISITOR_MODE keeps
    # its auto-ring timer through a failed transition, so the session still moves.
    machine, collector, store = _machine(visitor_mode_auto_ring_s=0.05)
    real_save = store.save_with_delivery_events

    async def run() -> None:
        machine.handle_button_pressed()
        assert machine.state == SessionState.VISITOR_MODE
        monkeypatch.setattr(store, "save_with_delivery_events", _disk_full)

        with pytest.raises(sqlite3.OperationalError):
            machine.transition(SessionState.RINGING, "test:ring")

        assert machine.state == SessionState.VISITOR_MODE
        assert collector.states() == ["BUTTON_PRESSED", "VISITOR_MODE"]
        monkeypatch.setattr(store, "save_with_delivery_events", real_save)
        await asyncio.sleep(0.15)
        assert machine.state == SessionState.RINGING

    asyncio.run(run())


def test_a_clear_that_fails_keeps_the_ended_session_on_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SESSION_END -> IDLE deletes the row; if that fails, the machine must still be in
    # SESSION_END (with its linger timer), not in an IDLE the disk disagrees with.
    machine, collector, store = _machine()
    machine.handle_button_pressed()
    machine.transition(SessionState.SESSION_END, "test:end")
    monkeypatch.setattr(store, "clear_with_delivery_events", _disk_full)

    with pytest.raises(sqlite3.OperationalError):
        machine.transition(SessionState.IDLE, "test:idle")

    assert machine.state == SessionState.SESSION_END
    assert machine.session_id is not None
    assert collector.states()[-1] == "SESSION_END"
    persisted = store.load()
    assert persisted is not None
    assert persisted.state == SessionState.SESSION_END


# -- BUTTON_PRESSED cannot be a dead end -----------------------------------------


def test_button_pressed_has_an_inactivity_fallback() -> None:
    machine, collector, _ = _machine(inactivity_timeout_s=0.05)

    async def run() -> None:
        assert machine.transition(SessionState.BUTTON_PRESSED, "test:stranded")
        await asyncio.sleep(0.12)
        assert machine.state == SessionState.SESSION_END
        ended = [e for e in collector.events if e["type"] == "session.ended"]
        assert ended[-1]["payload"]["outcome"] == "abandoned"

    asyncio.run(run())


def test_a_restored_button_pressed_session_is_not_stranded() -> None:
    # A restart between the two halves of a press restores BUTTON_PRESSED; without a
    # timer the door would refuse every later press until someone reset it by hand.
    config = SessionConfig(db_path=":memory:", inactivity_timeout_s=0.05)
    store = SessionStore(":memory:")
    first = SessionMachine(config=config, store=store, on_event=lambda _e: None)
    first.set_boot_id_fn(lambda: "boot")
    assert first.transition(SessionState.BUTTON_PRESSED, "test:stranded")
    first._cancel_timer()

    second = SessionMachine(config=config, store=store, on_event=lambda _e: None)
    second.set_boot_id_fn(lambda: "boot")

    async def run() -> None:
        second.restore_from_persistence()
        assert second.state == SessionState.BUTTON_PRESSED
        await asyncio.sleep(0.12)
        assert second.state == SessionState.SESSION_END

    asyncio.run(run())


# -- the ESP32 consumer never dies silently ---------------------------------------


@pytest.fixture
def door_state(monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    state.__init__()
    state.startup()
    yield state
    state.shutdown()


class EventOnlyLink:
    """Just enough transport for the event-consumer lifecycle."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.streams = 0

    def events(self) -> Any:
        self.streams += 1

        async def stream() -> Any:
            while True:
                yield await self.queue.get()

        return stream()


class DyingLink(EventOnlyLink):
    """The first event stream raises; the second behaves."""

    def events(self) -> Any:
        if self.streams >= 1:
            return super().events()
        self.streams += 1

        async def stream() -> Any:
            raise RuntimeError("driver exploded")
            yield  # pragma: no cover — makes this an async generator

        return stream()


async def until(predicate: Any, *, what: str) -> None:
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(f"timed out waiting for: {what}")


def _press() -> Any:
    return example_event("door.button_pressed")


@pytest.mark.anyio
async def test_a_handler_exception_costs_one_press_not_the_consumer(
    door_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = EventOnlyLink()
    door_state.attach_esp32_transport(link)
    await asyncio.sleep(0)
    real_save = door_state.store.save_with_delivery_events
    monkeypatch.setattr(door_state.store, "save_with_delivery_events", _disk_full)

    link.queue.put_nowait(_press())
    await until(lambda: door_state.esp32_handler_errors == 1, what="the handler error")

    # The press was refused whole, the consumer is still reading, and the door says so.
    assert door_state.machine.state == SessionState.IDLE
    assert door_state.esp32_consumer_alive()
    assert door_state.esp32_handler_last_error == "OperationalError"
    client = TestClient(app)
    health = client.get("/health").json()
    assert health["status"] == "degraded"
    assert "esp32 event handler failed: OperationalError (1 errors)" in health["detail"]
    metrics = client.get("/metrics").text
    assert "door_api_esp32_handler_errors_total 1" in metrics
    assert "door_api_esp32_consumer_alive 1" in metrics

    # Disk back: the very next hardware press reaches VISITOR_MODE.
    monkeypatch.setattr(door_state.store, "save_with_delivery_events", real_save)
    link.queue.put_nowait(_press())
    await until(lambda: door_state.machine.state == SessionState.VISITOR_MODE, what="visitor mode")

    # Degraded while the error is recent, ok once the window has passed.
    assert client.get("/health").json()["status"] == "degraded"
    door_state.config = dataclasses.replace(door_state.config, esp32_handler_error_window_s=0.0)
    assert client.get("/health").json()["status"] == "ok"


@pytest.mark.anyio
async def test_a_dead_consumer_is_reported_and_restarted(
    door_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ESP32_RECONNECT_BASE_S", "0.01")
    door_state.config = SessionConfig.from_env()
    link = DyingLink()
    door_state.attach_esp32_transport(link)
    await until(lambda: door_state.esp32_consumer_deaths == 1, what="the consumer to die")

    # Between the death and the restart the door is honest about it.
    assert not door_state.esp32_consumer_alive()
    client = TestClient(app)
    health = client.get("/health").json()
    assert health["status"] == "degraded"
    assert "esp32 event consumer is not running" in health["detail"]
    assert "door_api_esp32_consumer_alive 0" in client.get("/metrics").text

    await asyncio.sleep(0.05)
    assert link.streams == 2, "the consumer was not restarted"
    assert door_state.esp32_consumer_alive()
    assert door_state.esp32_consumer_restarts == 1
    assert client.get("/health").json()["status"] == "ok"

    # And the restarted consumer is the one reading the bell.
    link.queue.put_nowait(_press())
    await until(lambda: door_state.machine.state == SessionState.VISITOR_MODE, what="visitor mode")


@pytest.mark.anyio
async def test_a_detached_consumer_is_not_restarted(door_state: Any) -> None:
    # The supervisor's own detach cancels the task; that must not count as a death or
    # spawn a consumer on a transport that is gone.
    link = EventOnlyLink()
    door_state.attach_esp32_transport(link)
    await asyncio.sleep(0)
    door_state.detach_esp32_transport()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert door_state.esp32_consumer_deaths == 0
    assert door_state.esp32_consumer_restarts == 0
    assert not door_state.esp32_consumer_alive()
    # No transport attached means nothing to read: not a health problem.
    assert TestClient(app).get("/health").json()["status"] == "ok"
