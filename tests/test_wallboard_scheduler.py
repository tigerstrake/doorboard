"""Wallboard scheduler pacing: boot stagger and per-job failure backoff.

Both are the shape ADR-0038 fixed for the relay poll, applied to the job loop.
Before this, every job fired in the same instant on every (re)start, and a job
whose remote feed was down retried at full rate forever -- the aircraft feed
being a 30s interval doing exactly that.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps/wallboard-worker/src"))

from wallboard_worker.scheduler import ScheduledJob, Scheduler  # noqa: E402
from wallboard_worker.settings import Settings  # noqa: E402


class FakeClock:
    """Virtual time. `sleep` advances it; the scheduler is stopped at `stop_at`."""

    def __init__(self, stop_at: float) -> None:
        self.now = 0.0
        self._stop_at = stop_at
        self.scheduler: Scheduler | None = None

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.now > self._stop_at and self.scheduler is not None:
            self.scheduler.stop()


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        WALLBOARD_WORKER_HEARTBEAT_PATH=str(tmp_path / "heartbeat"),
        **overrides,
    )


def _run(settings: Settings, jobs: list[ScheduledJob], clock: FakeClock) -> None:
    scheduler = Scheduler(settings, jobs=jobs, monotonic=clock.monotonic, sleep=clock.sleep)
    clock.scheduler = scheduler
    scheduler.run_forever()


# --- boot stagger -----------------------------------------------------------


def test_jobs_do_not_all_fire_in_the_same_instant_on_start(tmp_path):
    """A crash-looping worker otherwise sends one burst of external calls per restart."""
    clock = FakeClock(stop_at=20.0)
    fired: list[tuple[float, str]] = []
    jobs = [
        ScheduledJob(f"job-{i}", 300.0, lambda i=i: fired.append((clock.now, f"job-{i}")))
        for i in range(4)
    ]
    _run(_settings(tmp_path, WALLBOARD_SCHEDULER_STAGGER_S=2.0), jobs, clock)

    assert [t for t, _ in fired] == [0.0, 2.0, 4.0, 6.0]
    # Every job still ran, and in declaration order -- the stagger delays the
    # burst, it does not drop or reorder work.
    assert [name for _, name in fired] == ["job-0", "job-1", "job-2", "job-3"]


def test_the_stagger_is_short_enough_to_fill_a_cold_wallboard_promptly(tmp_path):
    """The default must not leave tiles blank for minutes after a reboot."""
    settings = _settings(tmp_path)
    assert settings.scheduler_stagger_s * 8 <= 30.0


def test_a_zero_stagger_restores_the_all_at_once_behaviour(tmp_path):
    """Escape hatch: the stagger is a default, not a law."""
    clock = FakeClock(stop_at=5.0)
    fired: list[float] = []
    jobs = [ScheduledJob(f"job-{i}", 300.0, lambda: fired.append(clock.now)) for i in range(3)]
    _run(_settings(tmp_path, WALLBOARD_SCHEDULER_STAGGER_S=0.0), jobs, clock)

    assert fired == [0.0, 0.0, 0.0]


# --- failure backoff --------------------------------------------------------


def _failing_job(clock: FakeClock, calls: list[float], *, fail_first: int) -> ScheduledJob:
    def run() -> None:
        calls.append(clock.now)
        if len(calls) <= fail_first:
            raise RuntimeError("feed is down")

    return ScheduledJob("flaky", 10.0, run)


def test_a_failing_job_backs_off_instead_of_retrying_at_full_rate(tmp_path):
    """10s interval: retries at +20, +40, +80 rather than every 10s forever."""
    clock = FakeClock(stop_at=200.0)
    calls: list[float] = []
    settings = _settings(tmp_path, WALLBOARD_SCHEDULER_STAGGER_S=0.0)
    _run(settings, [_failing_job(clock, calls, fail_first=99)], clock)

    assert calls == [0.0, 20.0, 60.0, 140.0]


def test_the_backoff_is_capped(tmp_path):
    """Doubling forever would strand a recovered feed off-screen for hours."""
    clock = FakeClock(stop_at=400.0)
    calls: list[float] = []
    settings = _settings(
        tmp_path,
        WALLBOARD_SCHEDULER_STAGGER_S=0.0,
        WALLBOARD_SCHEDULER_BACKOFF_MAX_S=45.0,
    )
    _run(settings, [_failing_job(clock, calls, fail_first=99)], clock)

    gaps = [round(b - a, 3) for a, b in zip(calls, calls[1:], strict=False)]
    assert gaps[:2] == [20.0, 40.0]
    assert set(gaps[2:]) == {45.0}, "should sit at the cap, not keep doubling"


def test_the_backoff_resets_once_the_job_succeeds(tmp_path):
    """A recovered feed returns to its normal cadence, not a permanently slowed one."""
    clock = FakeClock(stop_at=95.0)
    calls: list[float] = []
    settings = _settings(tmp_path, WALLBOARD_SCHEDULER_STAGGER_S=0.0)
    _run(settings, [_failing_job(clock, calls, fail_first=2)], clock)

    # fail@0 -> +20, fail@20 -> +40, ok@60 -> back to the 10s interval
    assert calls == [0.0, 20.0, 60.0, 70.0, 80.0, 90.0]


def test_a_job_returning_none_is_not_treated_as_a_failure(tmp_path):
    """ADR-0039's countdown returns None when its table is exhausted.

    That is a job deciding it has nothing to publish, not a failure -- backing it
    off would slow a job that is working exactly as designed.
    """
    clock = FakeClock(stop_at=45.0)
    calls: list[float] = []

    def run() -> None:
        calls.append(clock.now)
        return None

    settings = _settings(tmp_path, WALLBOARD_SCHEDULER_STAGGER_S=0.0)
    _run(settings, [ScheduledJob("quiet", 10.0, run)], clock)

    assert calls == [0.0, 10.0, 20.0, 30.0, 40.0]


def test_one_jobs_backoff_does_not_slow_another(tmp_path):
    """Failure isolation is per job; a dead aircraft feed must not pace the birds."""
    clock = FakeClock(stop_at=45.0)
    healthy: list[float] = []
    broken: list[float] = []

    def ok() -> None:
        healthy.append(clock.now)

    def boom() -> None:
        broken.append(clock.now)
        raise RuntimeError("feed is down")

    settings = _settings(tmp_path, WALLBOARD_SCHEDULER_STAGGER_S=0.0)
    _run(
        settings,
        [ScheduledJob("healthy", 10.0, ok), ScheduledJob("broken", 10.0, boom)],
        clock,
    )

    assert healthy == [0.0, 10.0, 20.0, 30.0, 40.0]
    assert broken == [0.0, 20.0]


def test_run_once_still_runs_every_job_despite_failures(tmp_path):
    """The one-shot CLI path keeps its all-jobs-regardless contract."""
    ran: list[str] = []

    def boom() -> None:
        ran.append("broken")
        raise RuntimeError("feed is down")

    scheduler = Scheduler(
        _settings(tmp_path),
        jobs=[
            ScheduledJob("broken", 10.0, boom),
            ScheduledJob("healthy", 10.0, lambda: ran.append("healthy")),
        ],
    )
    scheduler.run_once()

    assert ran == ["broken", "healthy"]


@pytest.mark.parametrize("bad", [-1.0, -0.01])
def test_a_negative_stagger_is_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        _settings(tmp_path, WALLBOARD_SCHEDULER_STAGGER_S=bad)


def test_a_zero_backoff_cap_is_rejected(tmp_path):
    """Zero would turn the backoff into a busy loop."""
    with pytest.raises(ValueError):
        _settings(tmp_path, WALLBOARD_SCHEDULER_BACKOFF_MAX_S=0.0)
