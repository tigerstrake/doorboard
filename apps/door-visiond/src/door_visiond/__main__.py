"""CLI entry point for door-visiond.

Usage:
    uv run python -m door_visiond
    uv run python -m door_visiond --host 127.0.0.1 --port 8081
"""

from __future__ import annotations

import argparse
import logging
import logging.config

import uvicorn
from doorboard_observability.redaction import redaction_filter

from door_visiond.logging_setup import get_logger
from door_visiond.settings import settings


def _configure_logging() -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                # Defense in depth: also scrub at the handler for any non-visiond
                # logger that reaches stdout (ADR-0009 E-3).
                "biometric_redaction": {"()": lambda: redaction_filter()},
            },
            "formatters": {
                "json": {
                    "()": "logging.Formatter",
                    "fmt": (
                        '{"time":"%(asctime)s","level":"%(levelname)s",'
                        '"service":"door-visiond","logger":"%(name)s",'
                        '"message":"%(message)s"}'
                    ),
                }
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json",
                    "filters": ["biometric_redaction"],
                }
            },
            "root": {"level": "INFO", "handlers": ["stdout"]},
        }
    )
    # Ensure the door_visiond tree carries the filter regardless of config order.
    get_logger("door_visiond")


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(description="door-visiond service")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    cfg = settings()
    uvicorn.run(
        "door_visiond.app:app",
        host=args.host or cfg.host,
        port=args.port or cfg.port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
