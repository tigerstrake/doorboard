"""Provider factories shared by the CLI and scheduler."""

from __future__ import annotations

import logging

from birdnet.provider import (
    AvianVisitorsConfig,
    AvianVisitorsProvider,
    BirdnetConfig,
    BirdnetGoProvider,
    BirdProvider,
    MockBirdProvider,
)

from wallboard_worker.settings import Settings

logger = logging.getLogger("doorboard.wallboard_worker.providers")


def build_bird_provider(settings: Settings, *, force_mock: bool = False) -> BirdProvider:
    if (
        force_mock
        or settings.bird_provider == "mock"
        or (settings.bird_provider == "birdnet_go" and settings.birdnet_url == "mock")
    ):
        logger.info("bird_provider_selected", extra={"provider": "mock"})
        return MockBirdProvider()

    if settings.bird_provider == "avian_visitors":
        config = AvianVisitorsConfig(
            url=settings.avian_visitors_url,
            confidence_threshold=settings.birdnet_confidence_threshold,
            species_filter=settings.birdnet_species_filter,
            recent_hours=settings.avian_visitors_recent_hours,
            basic_user=settings.avian_visitors_basic_user,
            basic_password=settings.avian_visitors_basic_password,
            timeout_s=settings.avian_visitors_timeout_s,
        )
        logger.info("bird_provider_selected", extra={"provider": "avian_visitors"})
        return AvianVisitorsProvider(config)

    logger.info("bird_provider_selected", extra={"provider": "birdnet_go"})
    return BirdnetGoProvider(
        BirdnetConfig(
            url=settings.birdnet_url,
            confidence_threshold=settings.birdnet_confidence_threshold,
            species_filter=settings.birdnet_species_filter,
        )
    )
