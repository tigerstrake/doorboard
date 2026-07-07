from __future__ import annotations

import logging

import click
from birdnet.provider import BirdnetConfig, BirdnetGoProvider, MockBirdProvider

from wallboard_worker.jobs import run_bird_summary, run_daily_collage
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


@cli.command()
def daily_collage() -> None:
    """Run the daily collage generation job."""
    settings = Settings()
    run_daily_collage(settings)


if __name__ == "__main__":
    cli()
