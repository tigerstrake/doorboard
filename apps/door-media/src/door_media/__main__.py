"""CLI entry point for door-media.

Usage:
    uv run python -m door_media
    uv run python -m door_media --host 127.0.0.1 --port 8082

In production, the systemd unit uses the default bind from DOOR_MEDIA_BIND.
"""

from __future__ import annotations

import argparse
import logging
import logging.config

import uvicorn

from door_media.settings import settings


def _configure_logging() -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": "logging.Formatter",
                    "fmt": (
                        '{"time":"%(asctime)s","level":"%(levelname)s",'
                        '"service":"door-media","logger":"%(name)s",'
                        '"message":"%(message)s"}'
                    ),
                }
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json",
                }
            },
            "root": {"level": "INFO", "handlers": ["stdout"]},
        }
    )


def main() -> None:
    _configure_logging()

    parser = argparse.ArgumentParser(description="door-media service")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    cfg = settings()
    host = args.host or cfg.host
    port = args.port or cfg.port

    uvicorn.run(
        "door_media.app:app",
        host=host,
        port=port,
        log_config=None,  # we configure logging ourselves
        access_log=False,
    )


if __name__ == "__main__":
    main()
