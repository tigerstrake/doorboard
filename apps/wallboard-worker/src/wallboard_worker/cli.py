from __future__ import annotations

import logging
import logging.config
import signal
from collections.abc import Callable
from typing import Any, cast

import click
from aircraft.enrichment import AircraftEnricher, EnrichmentConfig
from aircraft.provider import AircraftConfig, MockAircraftProvider, OpenSkyAircraftProvider
from birdnet.provider import BirdnetConfig, BirdnetGoProvider, MockBirdProvider
from doorboard_observability.logging_json import json_logging_config
from printer.provider import (
    MockPrinterProvider,
    OctoPrintProvider,
    PrinterConfig,
    PrinterState,
)
from satellites.provider import MockSatelliteProvider, SatelliteConfig, SkyfieldSatelliteProvider

from wallboard_worker.jobs import (
    run_aircraft_summary,
    run_bird_summary,
    run_food_recommendation,
    run_printer_status,
    run_satellite_orbits,
    run_satellite_passes,
)
from wallboard_worker.scheduler import Scheduler, build_food_provider
from wallboard_worker.settings import Settings

logger = logging.getLogger("doorboard.wallboard_worker.cli")

SignalHandler = Callable[[int, Any], Any] | int | None


def _configure_logging() -> None:
    # The same one-JSON-object-per-line formatter every other service uses, and for the
    # same reason: the scheduler attaches `job`, `duration_s`, `retry_in_s` and the like
    # via extra=, and basicConfig's default format threw every one of them away. A
    # "scheduled_job_timed_out" line that does not say which job is not a diagnostic.
    logging.config.dictConfig(json_logging_config("wallboard-worker"))


def install_stop_signal_handler(scheduler: Scheduler) -> SignalHandler:
    """Turn SIGTERM into scheduler.stop(); returns the handler it replaced.

    `docker stop` sends SIGTERM and waits ten seconds before SIGKILL. Python's default
    disposition for SIGTERM is to die on the spot, which is fine for the loop but wrong
    for a run mid-flight: the executors are abandoned, the heartbeat file keeps its last
    mtime, and the log ends without a word. stop() lets the loop finish its current pass
    and close the executors, and the loop checks the flag at least once a second.
    """

    def _stop(signum: int, _frame: Any) -> None:
        logger.info("scheduler_stop_requested", extra={"signal": signal.Signals(signum).name})
        scheduler.stop()

    return signal.signal(signal.SIGTERM, _stop)


@click.group()
def cli() -> None:
    """wallboard-worker background job scheduler/runner CLI."""
    _configure_logging()


@cli.command("run")
@click.option("--once", is_flag=True, help="Run every enabled job once and exit")
@click.option("--mock", is_flag=True, help="Force mock providers for enabled jobs")
def run_scheduler(once: bool, mock: bool) -> None:
    """Run enabled ambient jobs on their configured schedules."""
    scheduler = Scheduler(Settings(), force_mock=mock)
    if once:
        scheduler.run_once()
        return
    previous = install_stop_signal_handler(scheduler)
    try:
        scheduler.run_forever()
    except KeyboardInterrupt:
        scheduler.stop()
    finally:
        signal.signal(signal.SIGTERM, previous)
    logger.info("scheduler_stopped")


@cli.command()
@click.option("--mock", is_flag=True, help="Force use of mock provider")
def bird_summary(mock: bool) -> None:
    """Run the bird summary ingestion job."""
    settings = Settings()

    # Use mock provider if forced, if URL is 'mock', or if feature is disabled
    # (Mocks are the fallback, matching: 'Mock mode always').
    if mock or settings.birdnet_url == "mock" or not settings.feature_birdnet:
        logger.info("Using MockBirdProvider")
        provider = MockBirdProvider()
    else:
        logger.info(f"Using BirdnetGoProvider at {settings.birdnet_url}")
        config = BirdnetConfig(
            url=settings.birdnet_url,
            confidence_threshold=settings.birdnet_confidence_threshold,
            species_filter=settings.birdnet_species_filter,
        )
        provider = BirdnetGoProvider(config)

    run_bird_summary(settings, provider)


def _build_satellite_provider(
    mock: bool, settings: Settings
) -> MockSatelliteProvider | SkyfieldSatelliteProvider:
    if mock or not settings.feature_satellites:
        logger.info("Using MockSatelliteProvider")
        return MockSatelliteProvider()
    logger.info("Using SkyfieldSatelliteProvider")
    config = SatelliteConfig(
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
    return SkyfieldSatelliteProvider(config)


@cli.command()
@click.option("--mock", is_flag=True, help="Force use of mock provider")
def satellite_passes(mock: bool) -> None:
    """Run the satellite pass prediction ingestion job."""
    settings = Settings()
    run_satellite_passes(settings, _build_satellite_provider(mock, settings))


@cli.command()
@click.option("--mock", is_flag=True, help="Force use of mock provider")
def satellite_orbits(mock: bool) -> None:
    """Run the full-orbit satellite-track ingestion job (ADR-0041)."""
    settings = Settings()
    run_satellite_orbits(settings, _build_satellite_provider(mock, settings))


@cli.command()
@click.option("--mock", is_flag=True, help="Force use of mock provider")
def aircraft_summary(mock: bool) -> None:
    """Run the aircraft summary ingestion job."""
    settings = Settings()

    enricher: AircraftEnricher | None = None
    if mock or not settings.feature_aircraft:
        logger.info("Using MockAircraftProvider")
        provider = MockAircraftProvider()
    else:
        logger.info("Using OpenSkyAircraftProvider")
        config = AircraftConfig(
            observer_lat=settings.aircraft_observer_lat,
            observer_lon=settings.aircraft_observer_lon,
            bbox_half_size_lat=settings.aircraft_bbox_half_size_lat,
            bbox_half_size_lon=settings.aircraft_bbox_half_size_lon,
            opensky_client_id=settings.opensky_client_id,
            opensky_client_secret=settings.opensky_client_secret,
            opensky_url="https://opensky-network.org/api/states/all",
            poll_cooldown_seconds=settings.aircraft_poll_cooldown_seconds,
        )
        provider = OpenSkyAircraftProvider(config)
        if settings.aircraft_enrichment_enabled:
            enricher = AircraftEnricher(
                EnrichmentConfig(
                    enabled=True,
                    max_aircraft=settings.aircraft_enrichment_max,
                )
            )

    run_aircraft_summary(settings, provider, enricher=enricher)


@cli.command()
@click.option("--mock", is_flag=True, help="Force use of mock provider")
@click.option(
    "--state",
    type=click.Choice(["idle", "printing", "paused", "error", "offline"]),
    help="Force state for mock",
)
def printer_status(mock: bool, state: str | None) -> None:
    """Run the printer status ingestion job."""
    settings = Settings()

    if mock or not settings.feature_printer:
        logger.info("Using MockPrinterProvider")
        # `state` is constrained to the PrinterState literals by click.Choice above.
        provider = MockPrinterProvider(force_state=cast("PrinterState | None", state))
    else:
        logger.info("Using OctoPrintProvider")
        config = PrinterConfig(
            octoprint_url=settings.octoprint_url,
            octoprint_api_key=settings.octoprint_api_key,
        )
        provider = OctoPrintProvider(config)

    run_printer_status(settings, provider)


@cli.command()
@click.option("--mock", is_flag=True, help="Force use of mock provider")
def food_recommendation(mock: bool) -> None:
    """Run the food recommendation ingestion job."""
    settings = Settings()
    provider = build_food_provider(settings, force_mock=mock)
    run_food_recommendation(settings, provider)


if __name__ == "__main__":
    cli()
