"""Monotonic, failure-isolated scheduler for ambient wallboard jobs."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from aircraft.enrichment import AircraftEnricher, EnrichmentConfig
from aircraft.provider import AircraftConfig, MockAircraftProvider, OpenSkyAircraftProvider
from birdnet.provider import BirdnetConfig, BirdnetGoProvider, MockBirdProvider
from food_recommendation.provider import FoodRecommendationProvider, MockFoodRecommendationProvider
from food_recommendation.stanford.provider import StanfordDiningConfig, StanfordDiningProvider
from printer.provider import MockPrinterProvider, OctoPrintProvider, PrinterConfig
from satellites.provider import MockSatelliteProvider, SatelliteConfig, SkyfieldSatelliteProvider

from wallboard_worker.jobs import (
    run_aircraft_summary,
    run_bird_summary,
    run_daily_collage,
    run_food_recommendation,
    run_printer_status,
    run_satellite_passes,
)
from wallboard_worker.settings import Settings

logger = logging.getLogger("doorboard.wallboard_worker.scheduler")


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    interval_s: float
    run: Callable[[], object]


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

    @property
    def job_names(self) -> tuple[str, ...]:
        return tuple(job.name for job in self._jobs)

    def stop(self) -> None:
        self._stopped = True

    def run_once(self) -> None:
        self._heartbeat()
        for job in self._jobs:
            self._run_isolated(job)

    def run_forever(self) -> None:
        next_runs = {job.name: self._monotonic() for job in self._jobs}
        logger.info("scheduler_started", extra={"jobs": list(next_runs)})
        while not self._stopped:
            self._heartbeat()
            now = self._monotonic()
            for job in self._jobs:
                if now >= next_runs[job.name]:
                    self._run_isolated(job)
                    next_runs[job.name] = self._monotonic() + job.interval_s

            if not next_runs:
                self._sleep(10.0)
                continue
            wait_s = max(0.01, min(1.0, min(next_runs.values()) - self._monotonic()))
            self._sleep(wait_s)

    def _run_isolated(self, job: ScheduledJob) -> None:
        started = self._monotonic()
        try:
            job.run()
        except Exception:
            logger.exception("scheduled_job_failed", extra={"job": job.name})
        else:
            logger.info(
                "scheduled_job_completed",
                extra={"job": job.name, "duration_s": self._monotonic() - started},
            )

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
        jobs.extend(
            [
                ScheduledJob(
                    "bird-summary",
                    settings.bird_summary_interval_s,
                    lambda: run_bird_summary(settings, bird),
                ),
                ScheduledJob(
                    "daily-collage",
                    settings.collage_interval_s,
                    lambda: run_daily_collage(settings),
                ),
            ]
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

    if settings.feature_aircraft:
        aircraft = (
            MockAircraftProvider()
            if force_mock
            else OpenSkyAircraftProvider(
                AircraftConfig(
                    observer_lat=settings.aircraft_observer_lat,
                    observer_lon=settings.aircraft_observer_lon,
                    bbox_half_size_lat=settings.aircraft_bbox_half_size_lat,
                    bbox_half_size_lon=settings.aircraft_bbox_half_size_lon,
                    opensky_client_id=settings.opensky_client_id,
                    opensky_client_secret=settings.opensky_client_secret,
                    opensky_url="https://opensky-network.org/api/states/all",
                    poll_cooldown_seconds=settings.aircraft_poll_cooldown_seconds,
                )
            )
        )
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
                    camera_stream_url=settings.printer_camera_stream_url,
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

    if settings.feature_food:
        food = build_food_provider(settings, force_mock=force_mock)
        jobs.append(
            ScheduledJob(
                "food-recommendation",
                settings.food_interval_s,
                lambda: run_food_recommendation(settings, food),
            )
        )
    return jobs


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
