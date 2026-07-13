from __future__ import annotations

import logging
from typing import cast

import click
from aircraft.provider import AircraftConfig, MockAircraftProvider, OpenSkyAircraftProvider
from food_recommendation.provider import MockFoodRecommendationProvider
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
    run_daily_collage,
    run_food_recommendation,
    run_printer_status,
    run_satellite_passes,
)
from wallboard_worker.providers import build_bird_provider
from wallboard_worker.scheduler import Scheduler
from wallboard_worker.settings import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("doorboard.wallboard_worker.cli")


@click.group()
def cli() -> None:
    """wallboard-worker background job scheduler/runner CLI."""
    pass


@cli.command()
@click.option("--mock", is_flag=True, help="Force use of mock provider")
def bird_summary(mock: bool) -> None:
    """Run the bird summary ingestion job."""
    settings = Settings()

    provider = build_bird_provider(settings, force_mock=mock or not settings.feature_birdnet)
    run_bird_summary(settings, provider)


@cli.command()
@click.option("--mock", is_flag=True, help="Force use of mock providers")
@click.option("--once", is_flag=True, help="Run every enabled job once and exit")
def run(mock: bool, once: bool) -> None:
    """Run the configured wallboard job scheduler."""
    scheduler = Scheduler(Settings(), force_mock=mock)
    if once:
        scheduler.run_once()
    else:
        scheduler.run_forever()


@cli.command()
def daily_collage() -> None:
    """Run the daily collage generation job."""
    settings = Settings()
    run_daily_collage(settings)


@cli.command()
@click.option("--mock", is_flag=True, help="Force use of mock provider")
def satellite_passes(mock: bool) -> None:
    """Run the satellite pass prediction ingestion job."""
    settings = Settings()

    if mock or not settings.feature_satellites:
        logger.info("Using MockSatelliteProvider")
        provider = MockSatelliteProvider()
    else:
        logger.info("Using SkyfieldSatelliteProvider")
        config = SatelliteConfig(
            watchlist=settings.satellites_watchlist,
            observer_lat=settings.satellites_observer_lat,
            observer_lon=settings.satellites_observer_lon,
            observer_elevation=settings.satellites_observer_elevation,
            min_elevation=settings.satellites_min_elevation,
            tle_url=settings.satellites_tle_url,
            tle_cache_path=settings.satellites_tle_cache_path,
        )
        provider = SkyfieldSatelliteProvider(config)

    run_satellite_passes(settings, provider)


@cli.command()
@click.option("--mock", is_flag=True, help="Force use of mock provider")
def aircraft_summary(mock: bool) -> None:
    """Run the aircraft summary ingestion job."""
    settings = Settings()

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
            opensky_username=settings.opensky_username,
            opensky_password=settings.opensky_password,
            opensky_url="https://opensky-network.org/api/states/all",
            poll_cooldown_seconds=settings.aircraft_poll_cooldown_seconds,
        )
        provider = OpenSkyAircraftProvider(config)

    run_aircraft_summary(settings, provider)


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
            camera_stream_url=settings.printer_camera_stream_url,
        )
        provider = OctoPrintProvider(config)

    run_printer_status(settings, provider)


@cli.command()
@click.option("--mock", is_flag=True, help="Force use of mock provider")
def food_recommendation(mock: bool) -> None:
    """Run the daily food recommendation ingestion job."""
    settings = Settings()

    if not settings.feature_food and not mock:
        logger.info("FEATURE_FOOD disabled; using MockFoodRecommendationProvider")
    else:
        logger.info("Using MockFoodRecommendationProvider")
    provider = MockFoodRecommendationProvider()

    run_food_recommendation(settings, provider)


if __name__ == "__main__":
    cli()
