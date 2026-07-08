from __future__ import annotations

import logging

import click
from food_recommendation.provider import MockFoodRecommendationProvider

from wallboard_worker.jobs import run_food_recommendation
from wallboard_worker.settings import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("doorboard.wallboard_worker.cli")


@click.group()
def cli() -> None:
    """wallboard-worker background job scheduler/runner CLI."""
    pass


@cli.command()
@click.option("--mock", is_flag=True, help="Force use of mock provider")
def food_recommendation(mock: bool) -> None:
    """Run the food recommendation ingestion job."""
    settings = Settings()

    # The interface is backend-agnostic, using MockFoodRecommendationProvider for MVP/mock mode.
    logger.info("Using MockFoodRecommendationProvider")
    provider = MockFoodRecommendationProvider()

    run_food_recommendation(settings, provider)


if __name__ == "__main__":
    cli()
