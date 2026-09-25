"""The `run` command's process-level behaviour: how it logs, and how it stops."""

from __future__ import annotations

import importlib
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from doorboard_observability.logging_json import JsonLogFormatter
from wallboard_worker.scheduler import ScheduledJob, Scheduler
from wallboard_worker.settings import Settings

cli_module = importlib.import_module("wallboard_worker.cli")


@pytest.fixture
def sigterm_restored():
    """signal.signal() is process-global; put back whatever was there."""
    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)


def test_stop_is_idempotent_and_a_stopped_scheduler_never_sleeps(tmp_path: Path) -> None:
    scheduler = Scheduler(
        Settings(WALLBOARD_WORKER_HEARTBEAT_PATH=tmp_path / "hb"),
        jobs=[ScheduledJob("noop", 10.0, lambda: None)],
        sleep=lambda _s: pytest.fail("a stopped scheduler slept"),
    )
    scheduler.stop()
    scheduler.stop()
    scheduler.run_forever()  # returns at once, having closed nothing it had not opened


def test_sigterm_stops_the_real_loop_cleanly(tmp_path: Path, sigterm_restored: None) -> None:
    """A real SIGTERM through the real loop: the pass in progress finishes and the loop exits.

    The sleep hook delivers the signal the way the kernel would on `docker stop`.
    Python runs the handler as soon as os.kill returns, before the loop checks
    `_stopped` -- one tick, then out. Without the handler the process simply dies.
    """
    ticks: list[float] = []
    ran: list[str] = []

    def sleep(wait_s: float) -> None:
        ticks.append(wait_s)
        os.kill(os.getpid(), signal.SIGTERM)

    scheduler = Scheduler(
        Settings(WALLBOARD_WORKER_HEARTBEAT_PATH=tmp_path / "hb"),
        jobs=[ScheduledJob("noop", 10.0, lambda: ran.append("noop"))],
        sleep=sleep,
    )
    replaced = cli_module.install_stop_signal_handler(scheduler)
    assert signal.getsignal(signal.SIGTERM) is not replaced

    scheduler.run_forever()

    assert ran == ["noop"], "the pass that was in flight completed"
    assert len(ticks) == 1, "stopped on the very next check"


def test_run_installs_the_sigterm_handler_and_puts_it_back(
    monkeypatch: pytest.MonkeyPatch, sigterm_restored: None
) -> None:
    """The wiring in `run`, not just the helper: while run_forever is live, SIGTERM means
    stop(); once it returns, the previous disposition is restored."""
    seen: dict[str, Any] = {}
    before = signal.getsignal(signal.SIGTERM)

    class FakeScheduler:
        def __init__(self, settings: Settings, *, force_mock: bool = False) -> None:
            self.stops = 0

        def run_forever(self) -> None:
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler) and handler not in (signal.SIG_DFL, signal.SIG_IGN)
            handler(signal.SIGTERM, None)
            seen["stops_during_run"] = self.stops

        def stop(self) -> None:
            self.stops += 1

    monkeypatch.setattr(cli_module, "Scheduler", FakeScheduler)
    result = CliRunner().invoke(cli_module.cli, ["run"])

    assert result.exit_code == 0, result.output
    assert seen["stops_during_run"] == 1
    assert signal.getsignal(signal.SIGTERM) == before


def test_the_cli_logs_json_with_the_service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """basicConfig's default format dropped every extra= field the scheduler attaches."""

    class FakeScheduler:
        def __init__(self, settings: Settings, *, force_mock: bool = False) -> None:
            pass

        def run_once(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "Scheduler", FakeScheduler)
    result = CliRunner().invoke(cli_module.cli, ["run", "--once"])
    assert result.exit_code == 0, result.output

    formatters = [
        h.formatter
        for h in logging.getLogger().handlers
        if isinstance(h.formatter, JsonLogFormatter)
    ]
    assert formatters, "the root logger is not on the JSON formatter"
    record = logging.LogRecord(
        "doorboard.wallboard_worker.scheduler",
        logging.ERROR,
        __file__,
        1,
        "scheduled_job_timed_out",
        None,
        None,
    )
    record.job = "food-recommendation"
    record.timeout_s = 300.0
    line = json.loads(formatters[0].format(record))
    assert line["service"] == "wallboard-worker"
    assert line["message"] == "scheduled_job_timed_out"
    assert line["job"] == "food-recommendation"
    assert line["timeout_s"] == 300.0
