"""Entry point: `python -m door_voice [--log-level DEBUG]`."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging.config

from doorboard_observability.logging_json import json_logging_config

from door_voice.app import main_async

_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _configure_logging(level: str = "INFO") -> None:
    # Structured JSON like every other Pi service (CONTRIBUTING: service, event,
    # timestamps, error fields). basicConfig lines were the one outlier.
    logging.config.dictConfig(json_logging_config("door-voice", level=level))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="door-voice service")
    parser.add_argument("--log-level", default="INFO", choices=_LEVELS)
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main_async())


if __name__ == "__main__":
    main()
