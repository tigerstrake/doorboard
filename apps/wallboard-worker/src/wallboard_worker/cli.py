from __future__ import annotations

import logging

import click
from aircraft.provider import AircraftConfig, MockAircraftProvider, OpenSkyAircraftProvider

from wallboard_worker.jobs import run_aircraft_summary
from wallboard_worker.settings import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("doorboard.wallboard_worker.cli")


@click.group()
def cli() -> None:
    """wallboard-worker background job scheduler/runner CLI."""
    pass


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


if __name__ == "__main__":
    cli()
