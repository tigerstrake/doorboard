from __future__ import annotations

import logging

import click
from printer.provider import MockPrinterProvider, OctoPrintProvider, PrinterConfig

from wallboard_worker.jobs import run_printer_status
from wallboard_worker.settings import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("doorboard.wallboard_worker.cli")


@click.group()
def cli() -> None:
    """wallboard-worker background job scheduler/runner CLI."""
    pass


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
        provider = MockPrinterProvider(force_state=state)
    else:
        logger.info("Using OctoPrintProvider")
        config = PrinterConfig(
            octoprint_url=settings.octoprint_url,
            octoprint_api_key=settings.octoprint_api_key,
            camera_stream_url=settings.printer_camera_stream_url,
        )
        provider = OctoPrintProvider(config)

    run_printer_status(settings, provider)


if __name__ == "__main__":
    cli()
