"""CLI entry point for control-plane-api.

Usage:
    uv run python -m control_plane_api
    uv run python -m control_plane_api --host 0.0.0.0 --port 8090
"""

from __future__ import annotations

import argparse
import logging.config

import uvicorn

from control_plane_api.settings import settings


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
                        '"service":"control-plane-api","logger":"%(name)s",'
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

    parser = argparse.ArgumentParser(description="control-plane-api service")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    cfg = settings()
    host = args.host or cfg.host
    port = args.port or cfg.port

    uvicorn.run("control_plane_api.app:app", host=host, port=port, log_config=None)


if __name__ == "__main__":
    main()
