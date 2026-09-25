"""Monotonic, failure-isolated scheduler for ambient wallboard jobs."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass

from aircraft.enrichment import AircraftEnricher, EnrichmentConfig
from aircraft.provider import (
    AdsbFiAircraftProvider,
    AircraftConfig,
    AircraftProvider,
    MockAircraftProvider,
    OpenSkyAircraftProvider,
)
from birdnet.provider import BirdnetConfig, BirdnetGoProvider, MockBirdProvider
from food_recommendation.provider import FoodRecommendationProvider, MockFoodRecommendationProvider
from food_recommendation.stanford.provider import StanfordDiningConfig, StanfordDiningProvider
from printer.provider import MockPrinterProvider, OctoPrintProvider, PrinterConfig
from satellites.provider import MockSatelliteProvider, SatelliteConfig, SkyfieldSatelliteProvider

from wallboard_worker.jobs import (
    run_academic_countdown,
    run_aircraft_summary,
    run_bird_summary,
    run_food_recommendation,
    run_printer_status,
    run_satellite_orbits,
    run_satellite_passes,
)
from wallboard_worker.settings import Settings

logger = logging.getLogger("doorboard.wallboard_worker.scheduler")


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    interval_s: float
    run: Callable[[], object]
    # Per-job deadline; None means the scheduler's default (WALLBOARD_JOB_TIMEOUT_S).
    timeout_s: float | None = None


class Scheduler:
    def __init__(
        self,
        settings: Settings,
        *,
        force_mock: bool = False,
        jobs: list[ScheduledJob] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._jobs = jobs if jobs is not None else build_jobs(settings, force_mock=force_mock)
        self._monotonic = monotonic
        self._sleep = sleep
        self._stopped = False
        # Consecutive failures per job name, driving the backoff in _next_delay.
        self._failures: dict[str, int] = {}
        # One single-thread executor per job, so a job never overlaps itself, and the
        # run that last overran its deadline, so the loop can tell "still hung" from
        # "ready to try again" without ever queueing a second run behind a stuck one.
        self._executors: dict[str, ThreadPoolExecutor] = {}
        self._inflight: dict[str, Future[object]] = {}

    @property
    def job_names(self) -> tuple[str, ...]:
        return tuple(job.name for job in self._jobs)

    def stop(self) -> None:
        self._stopped = True

    def close(self) -> None:
        """Release the worker threads. A run still hung past its deadline is abandoned."""
        for executor in self._executors.values():
            executor.shutdown(wait=False, cancel_futures=True)
        self._executors.clear()
        self._inflight.clear()

    def run_once(self) -> None:
        self._heartbeat()
        try:
            for job in self._jobs:
                self._run_isolated(job)
        finally:
            self.close()

    def run_forever(self) -> None:
        # Staggered rather than all at `now`. Every job firing in the same instant on
        # every (re)start means a crash-looping worker emits a burst of external
        # requests per restart, at exactly the moment something is already wrong. The
        # stagger is seconds, so a cold start still fills the wallboard promptly --
        # which is the reason they all ran immediately in the first place.
        start = self._monotonic()
        next_runs = {
            job.name: start + index * self._settings.scheduler_stagger_s
            for index, job in enumerate(self._jobs)
        }
        logger.info("scheduler_started", extra={"jobs": list(next_runs)})
        try:
            while not self._stopped:
                self._heartbeat()
                now = self._monotonic()
                for job in self._jobs:
                    if now >= next_runs[job.name]:
                        ok = self._run_isolated(job)
                        next_runs[job.name] = self._monotonic() + self._next_delay(job, ok=ok)

                if not next_runs:
                    self._sleep(10.0)
                    continue
                wait_s = max(0.01, min(1.0, min(next_runs.values()) - self._monotonic()))
                self._sleep(wait_s)
        finally:
            self.close()

    def _run_isolated(self, job: ScheduledJob) -> bool:
        """Run one job under its deadline. False if it raised or overran; both back off.

        The job runs on its own thread and the loop waits on it for at most
        `timeout_s`. A thread cannot be killed, so an overrun is *abandoned*, not
        stopped: the loop moves on to the other tiles (the heartbeat keeps ticking, the
        container stays healthy), and the next attempt is refused while the old run is
        still going rather than queued behind it. Before this, every job ran inline and
        one hung provider -- skyfield downloading an ephemeris with no timeout, a scrape
        that never returned -- froze every tile at its last value, indefinitely, because
        Compose does not restart a container for being unhealthy.
        """
        started = self._monotonic()
        pending = self._inflight.get(job.name)
        if pending is not None and not pending.done():
            logger.error("scheduled_job_still_running", extra={"job": job.name})
            return False

        timeout_s = job.timeout_s if job.timeout_s is not None else self._settings.job_timeout_s
        executor = self._executors.get(job.name)
        if executor is None:
            executor = self._executors[job.name] = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=f"job-{job.name}"
            )
        future = executor.submit(job.run)
        self._inflight[job.name] = future
        try:
            future.result(timeout=timeout_s)
        except FutureTimeoutError:
            logger.error("scheduled_job_timed_out", extra={"job": job.name, "timeout_s": timeout_s})
            return False
        except Exception:
            logger.exception("scheduled_job_failed", extra={"job": job.name})
            return False
        finally:
            if future.done():
                self._inflight.pop(job.name, None)
        logger.info(
            "scheduled_job_completed",
            extra={"job": job.name, "duration_s": self._monotonic() - started},
        )
        return True

    def _next_delay(self, job: ScheduledJob, *, ok: bool) -> float:
        """The job's interval on success; a doubling backoff while it keeps raising.

        Without this, a job whose remote dependency is down retried at full rate
        forever -- the same unbounded-retry shape ADR-0038 fixed for the relay poll.
        A provider that is rate-limiting or refusing gets hit hardest exactly when it
        least wants to be, and the aircraft feed is a 30s interval doing that.

        Backs off on an *exception* only. A job returning None is stating that it has
        nothing to publish -- an exhausted date table (ADR-0039), no aircraft in
        range -- which is correct behaviour, not a failure to punish.
        """
        if ok:
            if self._failures.pop(job.name, 0):
                logger.info("scheduled_job_recovered", extra={"job": job.name})
            return job.interval_s

        failures = self._failures[job.name] = self._failures.get(job.name, 0) + 1
        delay = min(job.interval_s * 2**failures, self._settings.scheduler_backoff_max_s)
        logger.warning(
            "scheduled_job_backoff",
            extra={"job": job.name, "consecutive_failures": failures, "retry_in_s": delay},
        )
        return delay

    def _heartbeat(self) -> None:
        try:
            self._settings.scheduler_heartbeat_path.touch(exist_ok=True)
        except OSError:
            logger.exception("scheduler_heartbeat_failed")


def build_jobs(settings: Settings, *, force_mock: bool = False) -> list[ScheduledJob]:
    jobs: list[ScheduledJob] = []
    if settings.feature_birdnet:
        bird = (
            MockBirdProvider()
            if force_mock or settings.birdnet_url == "mock"
            else BirdnetGoProvider(
                BirdnetConfig(
                    url=settings.birdnet_url,
                    confidence_threshold=settings.birdnet_confidence_threshold,
                    species_filter=settings.birdnet_species_filter,
                )
            )
        )
        jobs.append(
            ScheduledJob(
                "bird-summary",
                settings.bird_summary_interval_s,
                lambda: run_bird_summary(settings, bird),
            )
        )

    if settings.feature_satellites:
        satellite = (
            MockSatelliteProvider()
            if force_mock
            else SkyfieldSatelliteProvider(
                SatelliteConfig(
                    watchlist=settings.satellites_watchlist,
                    observer_lat=settings.satellites_observer_lat,
                    observer_lon=settings.satellites_observer_lon,
                    observer_elevation=settings.satellites_observer_elevation,
                    min_elevation=settings.satellites_min_elevation,
                    tle_url=settings.satellites_tle_url,
                    tle_cache_path=settings.satellites_tle_cache_path,
                    ephemeris_dir=settings.satellites_ephemeris_dir,
                    orbit_norad_ids=settings.satellites_orbit_norad_ids,
                    orbit_tle_url=settings.satellites_orbit_tle_url,
                    orbit_tle_cache_path=settings.satellites_orbit_tle_cache_path,
                    orbit_samples=settings.satellites_orbit_samples,
                )
            )
        )
        jobs.append(
            ScheduledJob(
                "satellite-passes",
                settings.satellite_interval_s,
                lambda: run_satellite_passes(settings, satellite),
            )
        )
        jobs.append(
            ScheduledJob(
                "satellite-orbits",
                settings.satellite_orbits_interval_s,
                lambda: run_satellite_orbits(settings, satellite),
            )
        )

    if settings.feature_aircraft:
        aircraft = build_aircraft_provider(settings, force_mock=force_mock)
        # One long-lived enricher per process so its TTL caches persist across
        # polls. force_mock keeps CI/dev fully offline (mock planes carry no
        # icao24, so the enricher would no-op anyway).
        aircraft_enricher = (
            None
            if force_mock or not settings.aircraft_enrichment_enabled
            else AircraftEnricher(
                EnrichmentConfig(
                    enabled=True,
                    max_aircraft=settings.aircraft_enrichment_max,
                )
            )
        )
        jobs.append(
            ScheduledJob(
                "aircraft-summary",
                settings.aircraft_interval_s,
                lambda: run_aircraft_summary(settings, aircraft, enricher=aircraft_enricher),
            )
        )

    if settings.feature_printer:
        printer = (
            MockPrinterProvider()
            if force_mock
            else OctoPrintProvider(
                PrinterConfig(
                    octoprint_url=settings.octoprint_url,
                    octoprint_api_key=settings.octoprint_api_key,
                )
            )
        )
        jobs.append(
            ScheduledJob(
                "printer-status",
                settings.printer_interval_s,
                lambda: run_printer_status(settings, printer),
            )
        )

    if settings.feature_academic_countdown:
        jobs.append(
            ScheduledJob(
                "academic-countdown",
                settings.academic_countdown_interval_s,
                lambda: run_academic_countdown(settings),
            )
        )

    if settings.feature_food:
        food = build_food_provider(settings, force_mock=force_mock)
        jobs.append(
            ScheduledJob(
                "food-recommendation",
                settings.food_interval_s,
                lambda: run_food_recommendation(settings, food),
                # Scrapes every hall in series and may wait on an LLM: longer deadline.
                timeout_s=settings.food_job_timeout_s,
            )
        )
    return jobs


def build_aircraft_provider(settings: Settings, *, force_mock: bool = False) -> AircraftProvider:
    """Pick the aircraft feed.

    `auto` (the default) chooses OpenSky when credentials are configured and adsb.fi
    otherwise. That is not a preference, it is a capability check: anonymous OpenSky allows a
    few hundred requests per IP per day and answered HTTP 429 to every poll on the owner's
    door — the API itself reporting a retry-after of nearly five hours. No polling cadence
    fixes that, so an unconfigured door needs a feed that will answer.

    OpenSky stays preferred once credentials exist: it is global and community-run rather than
    dependent on a single aggregator.
    """
    if force_mock:
        return MockAircraftProvider()

    config = AircraftConfig(
        observer_lat=settings.aircraft_observer_lat,
        observer_lon=settings.aircraft_observer_lon,
        bbox_half_size_lat=settings.aircraft_bbox_half_size_lat,
        bbox_half_size_lon=settings.aircraft_bbox_half_size_lon,
        opensky_client_id=settings.opensky_client_id,
        opensky_client_secret=settings.opensky_client_secret,
        opensky_url="https://opensky-network.org/api/states/all",
        poll_cooldown_seconds=settings.aircraft_poll_cooldown_seconds,
        radius_nm=settings.aircraft_radius_nm,
    )

    choice = (settings.aircraft_provider or "auto").strip().lower()
    if choice == "auto":
        choice = "opensky" if settings.opensky_client_id else "adsbfi"
    if choice == "opensky":
        logger.info("Using OpenSkyAircraftProvider")
        return OpenSkyAircraftProvider(config)
    if choice == "adsbfi":
        logger.info("Using AdsbFiAircraftProvider (no OpenSky credentials configured)")
        return AdsbFiAircraftProvider(config)
    msg = f"unknown AIRCRAFT_PROVIDER {settings.aircraft_provider!r}"
    raise ValueError(msg)


def build_food_provider(
    settings: Settings, *, force_mock: bool = False
) -> FoodRecommendationProvider:
    """Select the food recommendation provider from settings.

    Mock is the fallback whenever the feature is off, mocking is forced, or the
    real provider isn't selected. The real Stanford provider scrapes + optionally
    calls an LLM, so it only runs when explicitly configured (FOOD_PROVIDER=stanford).
    Shared by the daemon scheduler and the one-shot CLI so they stay in sync.
    """
    if force_mock or not settings.feature_food or settings.food_provider != "stanford":
        logger.info("Using MockFoodRecommendationProvider")
        return MockFoodRecommendationProvider()

    logger.info("Using StanfordDiningProvider (ai=%s)", settings.food_use_ai)
    config = StanfordDiningConfig(
        hall_ids=settings.food_hall_id_list(),
        meal_override=settings.food_meal_override or None,
        preferences_path=settings.food_preferences_path or None,
        use_ai=settings.food_use_ai,
        openai_api_key=settings.openai_api_key,
        openai_model=settings.openai_model,
    )
    return StanfordDiningProvider(config)
