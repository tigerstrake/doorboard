from __future__ import annotations

import logging

import click
from satellites.provider import MockSatelliteProvider, SatelliteConfig, SkyfieldSatelliteProvider

from wallboard_worker.jobs import run_satellite_passes
from wallboard_worker.settings import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("doorboard.wallboard_worker.cli")


@click.group()
def cli() -> None:
    """wallboard-worker background job scheduler/runner CLI."""
    pass


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


if __name__ == "__main__":
    cli()
