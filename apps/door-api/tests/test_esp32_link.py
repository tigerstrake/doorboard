"""The door controller link supervisor (T-101 hardware bring-up).

Two behaviours here are worth more than their line count.

The first is that **startup never depends on the controller**. door-api runs the
DoorPad, the wallboard and the visitor surfaces; if an unplugged ESP32 could delay
or fail startup, one loose USB cable would take the whole door down.

The second is that a **heartbeat timeout must not reopen the port**. On an
ESP32-S3-DevKitC the bridge's DTR line drives EN, so reopening resets the
controller and discards the cached profile it holds so it can answer the button
during a Pi outage (ADR-0006). "Controller went quiet" and "cable fell out" arrive
through the same `connected=False` callback and are told apart only by `reason`,
so a regression there would look like a working link that mysteriously forgets
everyone's colour.

The supervisor is driven through a fake link rather than a real serial port: the
port itself is exercised by tests/integration, and what needs pinning down here is
the decision-making.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Generator
from typing import Any

import pytest
from doorboard_esp32_link import Esp32LinkState

# Importing door_api.app builds the module-level state, which needs a database
# before it can be constructed. Same preamble as test_visitor_relay.py.
os.environ["DOOR_API_DB_PATH"] = ":memory:"
os.environ["DOOR_API_SOCIAL_DB_PATH"] = ":memory:"

from door_api.app import state  # noqa: E402
from door_api.config import SessionConfig  # noqa: E402
from door_api.esp32_link import (  # noqa: E402
    REOPEN_REASONS,
    Esp32LinkSettings,
    Esp32LinkSupervisor,
    parse_addr,
)


class FakeLink:
    """A transport whose link-state stream the test drives by hand."""

    def __init__(self) -> None:
        self.states: asyncio.Queue[Esp32LinkState] = asyncio.Queue()
        self.closes = 0

    def link_state_events(self) -> Any:
        async def stream() -> Any:
            while True:
                yield await self.states.get()

        return stream()

    async def close(self) -> None:
        self.closes += 1

    def push(self, *, connected: bool, reason: str) -> None:
        self.states.put_nowait(
            Esp32LinkState(connected=connected, changed_at_mono_ms=0, reason=reason)
        )


class Harness:
    """A supervisor plus the attach/detach record and a sleep that never waits."""

    def __init__(self, links: list[Any], *, fail_first: int = 0) -> None:
        self.links = links
        self.fail_first = fail_first
        self.opens = 0
        self.attached: list[Any] = []
        self.detaches = 0
        self.slept: list[float] = []
        self.supervisor = Esp32LinkSupervisor(
            opener=self._open,
            attach=self.attached.append,
            detach=self._detach,
            target="uart:/dev/fake@115200",
            reconnect_base_s=1.0,
            reconnect_max_s=8.0,
            sleep=self._sleep,
        )

    async def _open(self) -> Any:
        self.opens += 1
        if self.opens <= self.fail_first:
            raise OSError(2, "No such file or directory: '/dev/fake'")
        return self.links[min(self.opens - self.fail_first, len(self.links)) - 1]

    def _detach(self) -> None:
        self.detaches += 1

    async def _sleep(self, delay: float) -> None:
        self.slept.append(delay)
        await asyncio.sleep(0)


async def until(predicate: Any, *, what: str) -> None:
    """Yield to the loop until `predicate` holds. Everything here is instantaneous."""
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(f"timed out waiting for: {what}")


async def run_briefly(harness: Harness, predicate: Any, *, what: str) -> asyncio.Task[None]:
    task = asyncio.create_task(harness.supervisor.run())
    try:
        await until(predicate, what=what)
    except AssertionError:
        task.cancel()
        raise
    return task


async def stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# -- settings -----------------------------------------------------------------


def test_the_default_opens_nothing() -> None:
    # The default has to stay inert: this is what CI, dev machines and every
    # existing test see, and none of them have a door controller attached.
    config = SessionConfig(db_path=":memory:")
    assert config.esp32_transport == "mock"
    assert _settings(config).opener() is None
    assert _settings(config).enabled is False


def test_an_unknown_transport_fails_closed_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="door_api.esp32_link"):
        settings = _settings(SessionConfig(db_path=":memory:", esp32_transport="uartt"))
        assert settings.opener() is None
        assert settings.enabled is False

    # Silently ignoring a typo is the bug this whole task existed to fix.
    records = [r for r in caplog.records if r.message == "esp32_link_unknown_transport"]
    assert len(records) == 1, (
        "the typo should be announced once per startup, not per attribute read"
    )
    # Asserted on the structured field, not the formatted line: the value only
    # reaches a log reader because JsonLogFormatter emits `extra`.
    assert records[0].transport == "uartt"  # type: ignore[attr-defined]


def test_uart_settings_produce_an_opener_and_a_legible_target() -> None:
    settings = _settings(
        SessionConfig(db_path=":memory:", esp32_transport="uart", esp32_uart_device="/dev/ttyACM1")
    )
    assert settings.enabled is True
    assert settings.opener() is not None
    assert settings.describe() == "uart:/dev/ttyACM1@115200"


def test_udp_without_usable_addresses_is_refused_not_guessed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(
        SessionConfig(db_path=":memory:", esp32_transport="udp", esp32_udp_remote_addr="nonsense")
    )
    with caplog.at_level(logging.ERROR, logger="door_api.esp32_link"):
        assert settings.opener() is None
    assert "esp32_link_udp_addresses_invalid" in caplog.text


def test_udp_with_both_addresses_produces_an_opener() -> None:
    settings = _settings(
        SessionConfig(
            db_path=":memory:",
            esp32_transport="udp",
            esp32_udp_local_addr="0.0.0.0:9001",
            esp32_udp_remote_addr="192.168.1.50:9000",
        )
    )
    assert settings.opener() is not None
    assert settings.describe() == "udp:0.0.0.0:9001->192.168.1.50:9000"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("192.168.1.50:9000", ("192.168.1.50", 9000)),
        ("  door.local:1234 ", ("door.local", 1234)),
        ("192.168.1.50", None),
        ("192.168.1.50:", None),
        (":9000", None),
        ("host:port", None),
        ("", None),
    ],
)
def test_parse_addr(raw: str, expected: tuple[str, int] | None) -> None:
    assert parse_addr(raw) == expected


def _settings(config: SessionConfig) -> Esp32LinkSettings:
    return Esp32LinkSettings(
        transport=config.esp32_transport,
        uart_device=config.esp32_uart_device,
        uart_baud=config.esp32_uart_baud,
        udp_local_addr=config.esp32_udp_local_addr,
        udp_remote_addr=config.esp32_udp_remote_addr,
        reconnect_base_s=config.esp32_reconnect_base_s,
        reconnect_max_s=config.esp32_reconnect_max_s,
        door_id=config.door_id,
    )


# -- the supervisor -----------------------------------------------------------


@pytest.mark.anyio
async def test_a_live_link_is_attached_immediately() -> None:
    link = FakeLink()
    harness = Harness([link])
    task = await run_briefly(harness, lambda: harness.attached, what="the link to be attached")

    # Attached before the handshake, deliberately: outbound effects can queue and
    # the transport itself owns the retry.
    assert harness.attached == [link]

    link.push(connected=True, reason="hello")
    await until(lambda: harness.supervisor.connected, what="the handshake")
    assert harness.supervisor.connects == 1
    await stop(task)


@pytest.mark.anyio
async def test_a_quiet_controller_does_not_get_its_port_reopened() -> None:
    # The load-bearing case. Reopening resets the board over the DTR line, which
    # would discard the cached profile that exists for Pi outages (ADR-0006).
    link = FakeLink()
    harness = Harness([link])
    task = await run_briefly(harness, lambda: harness.attached, what="the link to be attached")
    link.push(connected=True, reason="hello")
    await until(lambda: harness.supervisor.connected, what="the handshake")

    link.push(connected=False, reason="heartbeat timeout")
    await until(lambda: harness.supervisor.idle_timeouts == 1, what="the idle timeout")

    # Counted and reported, but the port is untouched and still attached.
    assert harness.supervisor.connected is False
    assert link.closes == 0
    assert harness.detaches == 0
    assert harness.opens == 1

    # And it recovers by itself when frames resume — no reopen involved.
    link.push(connected=True, reason="heartbeat")
    await until(lambda: harness.supervisor.connected, what="recovery")
    assert harness.opens == 1
    await stop(task)


@pytest.mark.anyio
@pytest.mark.parametrize("reason", sorted(REOPEN_REASONS))
async def test_a_dead_descriptor_is_reopened(reason: str) -> None:
    first, second = FakeLink(), FakeLink()
    harness = Harness([first, second])
    task = await run_briefly(harness, lambda: harness.attached, what="the first attach")
    first.push(connected=True, reason="hello")
    await until(lambda: harness.supervisor.connects == 1, what="the handshake")

    first.push(connected=False, reason=reason)
    await until(lambda: harness.opens == 2, what="the reopen")

    # The old link is detached and closed before the new one is adopted, so the
    # event consumer can never be left reading a dead transport.
    assert harness.detaches == 1
    assert first.closes == 1
    assert harness.attached == [first, second]
    # A link that handshook earns a cheap retry rather than a punished one.
    assert harness.slept == [1.0]
    await stop(task)


@pytest.mark.anyio
async def test_a_missing_device_is_retried_with_capped_backoff() -> None:
    # An unplugged or unflashed board: door-api must keep running and keep trying,
    # without spinning a CPU the Hailo pipeline also needs.
    link = FakeLink()
    harness = Harness([link], fail_first=6)
    task = await run_briefly(harness, lambda: harness.attached, what="the eventual attach")

    assert harness.supervisor.open_failures == 6
    assert harness.slept == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]  # doubling, capped at 8
    await stop(task)


@pytest.mark.anyio
async def test_open_failures_never_escape_as_exceptions() -> None:
    class Boom(Harness):
        async def _open(self) -> Any:
            self.opens += 1
            raise RuntimeError("something unexpected inside the driver")

    harness = Boom([])
    task = asyncio.create_task(harness.supervisor.run())
    await until(lambda: harness.opens >= 3, what="a few attempts")
    # Still running: nothing propagated out of run().
    assert not task.done()
    await stop(task)


@pytest.mark.anyio
async def test_cancellation_releases_the_port() -> None:
    link = FakeLink()
    harness = Harness([link])
    task = await run_briefly(harness, lambda: harness.attached, what="the link to be attached")

    await stop(task)

    # Shutdown must not leave the fd open, or the next start cannot have it.
    assert link.closes == 1
    assert harness.detaches == 1


# -- integration with DoorApiState --------------------------------------------


@pytest.fixture
def door_state(monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    monkeypatch.setenv("DOOR_API_DB_PATH", ":memory:")
    monkeypatch.setenv("DOOR_API_SOCIAL_DB_PATH", ":memory:")
    state.__init__()
    # startup() rather than a hand-built state: start_esp32_link() is reached
    # through the same call the service makes, so these tests would notice it
    # being dropped from the sequence.
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


@pytest.mark.anyio
async def test_reattaching_starts_a_fresh_event_consumer(door_state: Any) -> None:
    # The subtle one. start_esp32_event_consumer() returns early when a task
    # already exists, so a detach that only cancelled the task without clearing
    # the handle would leave every reconnected link permanently undrained — the
    # button would work once, then silently stop.
    first, second = EventOnlyLink(), EventOnlyLink()

    door_state.attach_esp32_transport(first)
    await asyncio.sleep(0)
    assert first.streams == 1

    door_state.detach_esp32_transport()
    assert door_state.esp32_transport is None

    door_state.attach_esp32_transport(second)
    await asyncio.sleep(0)
    assert second.streams == 1, "the replacement link is being read"
    assert door_state.esp32_transport is second


@pytest.mark.anyio
async def test_an_injected_transport_is_not_fought_over(
    door_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The simulator and the integration tests inject a transport on purpose; the
    # supervisor must not open a competing serial port underneath them.
    monkeypatch.setenv("ESP32_TRANSPORT", "uart")
    door_state.config = SessionConfig.from_env()
    injected = EventOnlyLink()
    door_state.esp32_transport = injected

    door_state.start_esp32_link()

    assert door_state.esp32_link is None
    assert door_state.esp32_transport is injected


def test_the_default_configuration_starts_no_supervisor(door_state: Any) -> None:
    # startup() already ran in the fixture with a default (mock) config.
    assert door_state.esp32_link is None
    assert door_state.esp32_transport is None


@pytest.mark.anyio
async def test_startup_survives_a_device_that_is_not_there(
    door_state: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One loose USB cable must not take down the DoorPad, the wallboard or the
    # visitor surfaces.
    monkeypatch.setenv("ESP32_TRANSPORT", "uart")
    monkeypatch.setenv("ESP32_UART_DEVICE", "/dev/does-not-exist-doorboard")
    monkeypatch.setenv("ESP32_RECONNECT_BASE_S", "0.01")
    door_state.config = SessionConfig.from_env()

    door_state.start_esp32_link()  # must not raise

    assert door_state.esp32_link is not None
    await until(lambda: door_state.esp32_link.open_failures >= 1, what="the first open to fail")
    # Failing, but running and still trying.
    assert door_state.esp32_transport is None
    assert door_state.machine.snapshot() is not None


@pytest.mark.anyio
async def test_metrics_expose_what_bring_up_needs_to_know() -> None:
    link = FakeLink()
    harness = Harness([link], fail_first=1)
    task = await run_briefly(harness, lambda: harness.attached, what="the attach")
    link.push(connected=True, reason="hello")
    await until(lambda: harness.supervisor.connected, what="the handshake")

    metrics = harness.supervisor.metrics()
    assert metrics["door_api_esp32_link_connected"] == 1
    assert metrics["door_api_esp32_link_connects_total"] == 1
    assert metrics["door_api_esp32_link_open_failures_total"] == 1
    assert metrics["door_api_esp32_link_idle_timeouts_total"] == 0
    await stop(task)
